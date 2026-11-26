"""
Reconciler — applies a Recommendation by spawning or destroying replicas.

The two previous reconcilers diverged: ``services/autoscaler.py``
mutated ``Service.min_replicas`` (which is a HPA-style minimum replica
hint, not an actual replica count), while ``tasks_autoscale.py`` and
``views_autoscale.ScalingViewSet`` actually spawned/destroyed
``ServiceReplica`` rows via ``SpawningService``.

This module unifies on the actual replica-row approach (correct
semantics) and additionally updates ``last_scale_at`` for cooldown.
"""
import contextlib
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict

from django.conf import settings
from django.core.cache import cache as django_cache
from django.db import transaction
from django.utils import timezone

from .decision import Recommendation

logger = logging.getLogger(__name__)

# Service-level lock to prevent the two Celery beat tasks
# (check_autoscale_task + analyze_all_services_task) from concurrently
# spawning replicas for the same service. Keyed by service.id str.
# Bounded at MAX_LOCKS entries — least-recently-used entries are evicted
# to prevent unbounded memory growth on large platforms.
_MAX_LOCKS = 1000
_SPAWN_LOCKS: OrderedDict[str, threading.Lock] = OrderedDict()
_SPAWN_LOCKS_GUARD = threading.Lock()


def _lock_for(service_id: str) -> threading.Lock:
    with _SPAWN_LOCKS_GUARD:
        lock = _SPAWN_LOCKS.get(service_id)
        if lock is not None:
            _SPAWN_LOCKS.move_to_end(service_id)
            return lock
        # Evict oldest entries if at capacity — but NEVER a lock that is
        # currently held. Evicting a held lock hands out two Lock objects
        # for one service, letting two workers spawn concurrently
        # (split-brain double-spawn). If every lock is held (extreme
        # contention), overshoot the bound instead of splitting a brain.
        while len(_SPAWN_LOCKS) >= _MAX_LOCKS:
            for key, candidate in _SPAWN_LOCKS.items():
                if not candidate.locked():
                    del _SPAWN_LOCKS[key]
                    break
            else:
                break
        lock = threading.Lock()
        _SPAWN_LOCKS[service_id] = lock
        return lock


class ScaleResult:
    """Returned to callers so they can log/aggregate."""

    def __init__(self, recommendation: Recommendation, applied: bool,
                 spawned: int = 0, destroyed: int = 0, error: str | None = None):
        self.recommendation = recommendation
        self.applied = applied
        self.spawned = spawned
        self.destroyed = destroyed
        self.error = error

    def to_dict(self) -> dict:
        return {
            'applied': self.applied,
            'spawned': self.spawned,
            'destroyed': self.destroyed,
            'error': self.error,
            **self.recommendation.to_dict(),
        }


class Reconciler:
    """Apply a scaling decision to a service.

    Acquires a per-service in-process lock so the two Celery beat tasks
    that share the engine cannot double-spawn under concurrent dispatch.
    """

    def __init__(self, service, *, now=None):
        self.service = service
        self.now = now or timezone.now()
        self._lock = _lock_for(str(service.id))

    def apply(self, recommendation: Recommendation) -> ScaleResult:
        if recommendation.action == 'none':
            return ScaleResult(recommendation, applied=False)

        # K8s services are scaled by HPA — skip the custom Docker autoscaler.
        # HPA manages Deployment.spec.replicas directly; our ServiceReplica
        # rows are a Docker-only concept.
        if getattr(self.service, 'active_target_type', None) == 'kubernetes':
            return ScaleResult(recommendation, applied=False, error='k8s-hpa')

        # The threading.Lock serializes concurrent invocations within a
        # single worker process (the race test pins this: _scale_up calls
        # must never overlap for one service). It is held for the whole
        # apply, INCLUDING the Docker/SSH I/O below — that only blocks
        # same-service scalers, never the database.
        #
        # The DB transaction, on the other hand, is confined to
        # ``_plan_locked`` (row lock + recount + clamp, all fast). The
        # spawn/destroy I/O runs outside any transaction: holding a row
        # lock across multi-minute SSH calls starved every other scaler
        # for the service, and a rollback after successful spawns deleted
        # the DB rows while the containers kept running (leak).
        # Cross-process races from the shorter lock window are bounded by
        # the per-iteration headroom re-check in _scale_up plus the
        # pipeline dedup cache and spawning guards.
        with self._lock:
            dist_token = self._acquire_distributed_lock()
            if dist_token is None:
                return ScaleResult(
                    recommendation,
                    applied=False,
                    error='reconcile lock busy in another worker',
                )
            try:
                plan = self._plan_locked(recommendation)
                if isinstance(plan, ScaleResult):
                    return plan
                if plan.action == 'scale_up':
                    return self._scale_up(plan)
                if plan.action == 'scale_down':
                    return self._scale_down(plan)
                return ScaleResult(recommendation, applied=False)
            finally:
                self._release_distributed_lock(dist_token)

    def _dist_lock_key(self) -> str:
        return f"autoscale:reconcile:{self.service.id}"

    def _acquire_distributed_lock(self, ttl: int = 300, wait_seconds: float = 10.0) -> str | None:
        """Best-effort Redis cache lock across Celery prefork processes."""
        token = uuid.uuid4().hex
        key = self._dist_lock_key()
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                if django_cache.add(key, token, timeout=ttl):
                    return token
            except Exception:
                # If cache backend is unavailable, fall back to DB row lock.
                return ""
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def _release_distributed_lock(self, token: str) -> None:
        if not token:
            return
        key = self._dist_lock_key()
        try:
            if django_cache.get(key) == token:
                django_cache.delete(key)
        except Exception:
            pass

    @transaction.atomic
    def _plan_locked(self, rec: Recommendation) -> Recommendation | ScaleResult:
        """Short locked planning phase: recount under the row lock and
        clamp the recommendation. Returns a clamped Recommendation to
        execute, or a ScaleResult when there is nothing to do."""
        from apps.autoscaler.models.replica import ServiceReplica
        from apps.deployments.models.core import Service

        # Acquire the row lock for the read-decide-write cycle. Other
        # scale events for this service block here until we commit —
        # which is fast now that no I/O happens inside.
        try:
            locked = Service.objects.select_for_update().get(id=self.service.id)
        except Service.DoesNotExist:
            return ScaleResult(rec, applied=False, error='service not found after lock')
        # Use the fresh, locked instance downstream so ``last_scale_at``
        # and replica counts reflect any committed concurrent change.
        self.service = locked

        running = ServiceReplica.objects.filter(
            service=locked, status='RUNNING'
        ).count()
        spawning = ServiceReplica.objects.filter(
            service=locked, status__in=('SPAWNING', 'DRAINING')
        ).exists()

        # A spawn from another process may have committed between the
        # pipeline's decision and our acquiring the lock. Defer.
        if spawning:
            return ScaleResult(rec, applied=False, error='spawn in progress after lock')

        if rec.action == 'scale_up':
            max_r = locked.max_replicas or 1
            if running >= max_r:
                return ScaleResult(rec, applied=False, error='at max_replicas after lock')
            headroom = max_r - running
            up_by = min(rec.scale_up_by, headroom)
            if up_by <= 0:
                return ScaleResult(rec, applied=False, error='no headroom after lock')
            return Recommendation(
                action='scale_up', reason=rec.reason, scale_up_by=up_by,
                urgency=rec.urgency, cooldown_active=rec.cooldown_active,
                at_capacity=rec.at_capacity, spawning_in_progress=rec.spawning_in_progress,
            )

        if rec.action == 'scale_down':
            min_r = (
                0 if getattr(locked, 'sablier_enabled', False) is True
                else (locked.min_replicas or 0)
            )
            if running <= min_r:
                return ScaleResult(rec, applied=False, error='at min_replicas after lock')
            removable = running - min_r
            down_by = min(max(rec.scale_down_by, 1), removable)
            if down_by <= 0:
                return ScaleResult(rec, applied=False, error='no removable replicas after lock')
            return Recommendation(
                action='scale_down', reason=rec.reason, scale_down_by=down_by,
                urgency=rec.urgency, cooldown_active=rec.cooldown_active,
                at_capacity=rec.at_capacity, spawning_in_progress=rec.spawning_in_progress,
            )

        return ScaleResult(rec, applied=False)

    def _live_replica_count(self) -> int:
        """Current RUNNING + SPAWNING rows — the occupancy check used
        before every spawn so concurrent workers cannot overshoot max."""
        from apps.autoscaler.models.replica import ServiceReplica
        return ServiceReplica.objects.filter(
            service=self.service, status__in=('RUNNING', 'SPAWNING')
        ).count()

    def _has_headroom(self) -> bool:
        max_r = getattr(self.service, 'max_replicas', None) or 1
        return self._live_replica_count() < max_r

    @transaction.atomic
    def _reserve_spawning_replica(self, node, reason: str):
        """Atomically re-verify max_replicas under row lock and create a SPAWNING row."""
        from apps.autoscaler.models.replica import ServiceReplica
        from apps.deployments.models.core import Service

        locked = Service.objects.select_for_update().get(id=self.service.id)
        self.service = locked
        max_r = locked.max_replicas or 1
        live = ServiceReplica.objects.filter(
            service=locked, status__in=('RUNNING', 'SPAWNING')
        ).count()
        if live >= max_r:
            return None
        return ServiceReplica.objects.create(
            service=locked,
            node=node,
            spawn_reason=reason,
            status='SPAWNING',
        )

    def _resolve_primary_server(self):
        try:
            from apps.deployments.services.caddy_manager.config_generation import (
                _resolve_effective_server,
            )
            return _resolve_effective_server(self.service)
        except Exception:
            return getattr(self.service, 'server', None)

    def _spawn_loop_on_target(self, spawner, node, remaining: int, reason: str) -> tuple[int, bool]:
        """Attempt up to *remaining* spawns on *node* (None = local)."""
        spawned_here = 0
        while spawned_here < remaining:
            replica = self._reserve_spawning_replica(node, reason)
            if replica is None:
                logger.info("Headroom exhausted for %s — stopping scale-up", self.service.name)
                return spawned_here, False
            try:
                if node is None:
                    spawner.spawn_local(self.service, replica)
                else:
                    spawner.spawn(self.service, node, replica)
                spawned_here += 1
            except Exception as exc:
                where = "Local" if node is None else f"Remote ({node.name})"
                logger.warning("%s spawn failed for %s: %s", where, self.service.name, exc)
                try:
                    spawner.destroy(replica)
                except Exception as dest_exc:
                    logger.debug("Destroy replica after spawn failure: %s", dest_exc)
                replica.status = 'DESTROYED'
                replica.destroyed_at = timezone.now()
                replica.save(update_fields=['status', 'destroyed_at'])
                return spawned_here, True
        return spawned_here, True

    # ── Scale up ─────────────────────────────────────────────────────────────
    def _scale_up(self, rec: Recommendation) -> ScaleResult:
        from apps.deployments.models.core import ManagedServer
        from apps.deployments.services.node_scorer import NodeScorer, _get_min_score
        from apps.deployments.services.server_guard import ServerGuard
        from apps.deployments.services.spawning_service import SpawningService

        spawned = 0
        try:
            spawner = SpawningService()
            remaining = rec.scale_up_by
            primary_server = self._resolve_primary_server()
            is_remote_primary = bool(
                primary_server is not None
                and not getattr(primary_server, 'is_primary', True)
            )

            # Priority 1: scale on the service's own primary host first
            # (Master if local, or its assigned ManagedServer if remote).
            if not is_remote_primary:
                got, headroom_left = self._spawn_loop_on_target(
                    spawner, None, remaining, rec.reason
                )
                spawned += got
                if not headroom_left:
                    if spawned > 0:
                        self._record_scale()
                        return ScaleResult(rec, applied=True, spawned=spawned)
                    return ScaleResult(rec, applied=False, error='Headroom exhausted')
            elif getattr(primary_server, 'status', 'ONLINE') != 'OFFLINE':
                got, headroom_left = self._spawn_loop_on_target(
                    spawner, primary_server, remaining, rec.reason
                )
                spawned += got
                if not headroom_left:
                    if spawned > 0:
                        self._record_scale()
                        return ScaleResult(rec, applied=True, spawned=spawned)
                    return ScaleResult(rec, applied=False, error='Headroom exhausted')

            if spawned >= remaining:
                self._record_scale()
                return ScaleResult(rec, applied=True, spawned=spawned)

            # Priority 2: remote overflow nodes — spread replicas across
            # healthy remote servers when primary capacity is exhausted.
            _overflow = os.getenv("SMSLY_AUTOSCALE_REMOTE_OVERFLOW", "true").lower() in (
                "1", "true", "yes", "on"
            )
            if spawned < remaining and not getattr(self.service, 'vpa_enabled', False) and not _overflow:
                logger.info("Primary capacity exhausted for %s — remote overflow disabled by env", self.service.name)
                if spawned > 0:
                    self._record_scale()
                    return ScaleResult(rec, applied=True, spawned=spawned)
                return ScaleResult(rec, applied=False,
                                   error='Primary spawn failed and remote overflow is disabled')

            candidates_qs = ManagedServer.objects.filter(
                status=ManagedServer.Status.ONLINE,
                allow_user_workloads=True,
            ).exclude(is_primary=True)
            if is_remote_primary:
                candidates_qs = candidates_qs.exclude(id=primary_server.id)

            candidates = [
                n for n in candidates_qs
                if ServerGuard.check_user_workload_allowed(n).get('ok', False)
            ]
            if not candidates:
                logger.info("No remote overflow nodes available for %s", self.service.name)
                if spawned > 0:
                    self._record_scale()
                    return ScaleResult(rec, applied=True, spawned=spawned)
                return ScaleResult(rec, applied=False,
                                   error='No remote nodes available')

            scorer = NodeScorer()
            scores = scorer.score(candidates)
            min_score = _get_min_score()
            qualified = [(n, s, r) for n, s, r in scores if s >= min_score]
            fallback = [(n, s, r) for n, s, r in scores if 0 <= s < min_score]

            if not qualified and not fallback:
                logger.info(
                    "All %d remote nodes unreachable for %s",
                    len(scores), self.service.name,
                )
                if spawned > 0:
                    self._record_scale()
                    return ScaleResult(rec, applied=True, spawned=spawned)
                return ScaleResult(rec, applied=False,
                                   error='All remote nodes unreachable')

            nodes_to_try = qualified + fallback
            if not qualified:
                best_node, best_score, _ = fallback[0]
                logger.warning(
                    "All nodes below min_score=%d for %s — trying best "
                    "node %s (score=%.1f) as fallback",
                    min_score, self.service.name,
                    best_node.name, best_score,
                )

            for node, score, resources in nodes_to_try:
                if spawned >= remaining:
                    break
                got, headroom_left = self._spawn_loop_on_target(
                    spawner, node, remaining - spawned, rec.reason
                )
                if got > 0:
                    spawned += got
                    logger.info(
                        "Auto-scaled %s: spawned %d on %s (score=%.1f, "
                        "mem=%.0f%% cpu=%.0f%% disk=%.0f%%)",
                        self.service.name, got, node.name, score,
                        resources['mem'], resources['cpu'], resources['disk'],
                    )
                if not headroom_left:
                    break

            if spawned > 0:
                self._record_scale()
                return ScaleResult(rec, applied=True, spawned=spawned)
            return ScaleResult(rec, applied=False, error='All spawns failed')
        except Exception as exc:
            logger.warning("Reconciler.scale_up failed for %s: %s", self.service.name, exc)
            return ScaleResult(rec, applied=False, error=str(exc))

    # ── Scale down ───────────────────────────────────────────────────────────
    @transaction.atomic
    def _reserve_draining_replicas(self, target: int) -> list:
        """Atomically re-check min_replicas under row locks and mark replicas DRAINING."""
        from apps.autoscaler.models.replica import ServiceReplica
        from apps.deployments.models.core import Service

        locked = Service.objects.select_for_update().get(id=self.service.id)
        self.service = locked
        min_r = (
            0 if getattr(locked, 'sablier_enabled', False) is True
            else (locked.min_replicas or 0)
        )
        running_replicas = list(
            ServiceReplica.objects.select_for_update().filter(
                service=locked, status='RUNNING',
            ).order_by('created_at')
        )
        removable = len(running_replicas) - min_r
        if removable <= 0:
            return []
        to_drain = min(max(target, 1), removable)
        replicas = running_replicas[:to_drain]
        if not replicas:
            return []
        ServiceReplica.objects.filter(
            id__in=[r.id for r in replicas]
        ).update(status='DRAINING')
        for r in replicas:
            r.status = 'DRAINING'
        return replicas

    def _scale_down(self, rec: Recommendation) -> ScaleResult:
        from apps.deployments.services.spawning_service import SpawningService
        destroyed = 0
        target = max(rec.scale_down_by, 1)
        try:
            spawner = SpawningService()
            replicas = self._reserve_draining_replicas(target)
            if not replicas:
                return ScaleResult(rec, applied=False, error='No replicas to destroy')

            # Record DRAINING entry timestamp so stuck-spawn tracking ages
            # from drain start while in-flight requests drain before teardown.
            now_ts = time.time()
            for r in replicas:
                with contextlib.suppress(Exception):
                    django_cache.set(
                        f"autoscale:transitional:{r.id}:DRAINING",
                        now_ts,
                        timeout=3600,
                    )

            # Remove draining remote overflow nodes from Master Traefik /
            # Caddy before tearing down containers so new traffic drains away.
            with contextlib.suppress(Exception):
                from apps.deployments.services.spawning_service import (
                    _sync_routing_after_replica_change,
                )
                _sync_routing_after_replica_change(self.service)

            drain_s = 0.0 if getattr(settings, 'IS_TESTING', False) else max(
                0.0, float(os.environ.get('SCALE_DOWN_DRAIN_SECONDS', '3'))
            )
            if drain_s > 0:
                time.sleep(min(drain_s, 30.0))

            for replica in replicas:
                try:
                    spawner.destroy(replica)
                    destroyed += 1
                    logger.info("Auto-scaled down %s: destroyed replica %s",
                                self.service.name, replica.container_name)
                except Exception as exc:
                    logger.warning("Destroy failed for %s: %s", self.service.name, exc)
            if destroyed > 0:
                self._record_scale()
                return ScaleResult(rec, applied=True, destroyed=destroyed)
            return ScaleResult(rec, applied=False, error='No replicas to destroy')
        except Exception as exc:
            logger.warning("Reconciler.scale_down failed for %s: %s", self.service.name, exc)
            return ScaleResult(rec, applied=False, error=str(exc))

    # ── Cooldown write-back ─────────────────────────────────────────────────
    @transaction.atomic
    def _record_scale(self):
        from apps.deployments.models.core import Service
        Service.objects.filter(id=self.service.id).update(last_scale_at=self.now)
        self.service.last_scale_at = self.now
