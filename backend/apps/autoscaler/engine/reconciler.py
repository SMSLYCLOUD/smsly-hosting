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
import logging
import threading

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
_SPAWN_LOCKS: dict[str, threading.Lock] = {}
_SPAWN_LOCKS_ORDER: list[str] = []
_SPAWN_LOCKS_GUARD = threading.Lock()


def _lock_for(service_id: str) -> threading.Lock:
    with _SPAWN_LOCKS_GUARD:
        lock = _SPAWN_LOCKS.get(service_id)
        if lock is None:
            # Evict oldest entry if at capacity
            if len(_SPAWN_LOCKS) >= _MAX_LOCKS:
                oldest = _SPAWN_LOCKS_ORDER.pop(0)
                _SPAWN_LOCKS.pop(oldest, None)
            lock = threading.Lock()
            _SPAWN_LOCKS[service_id] = lock
            _SPAWN_LOCKS_ORDER.append(service_id)
        else:
            # Move to end (most recently used)
            try:
                _SPAWN_LOCKS_ORDER.remove(service_id)
            except ValueError:
                pass
            _SPAWN_LOCKS_ORDER.append(service_id)
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

        with self._lock:
            if recommendation.action == 'scale_up':
                return self._scale_up(recommendation)
            if recommendation.action == 'scale_down':
                return self._scale_down(recommendation)
            return ScaleResult(recommendation, applied=False)

    # ── Scale up ─────────────────────────────────────────────────────────────
    def _scale_up(self, rec: Recommendation) -> ScaleResult:
        from apps.deployments.models.core import ManagedServer
        from apps.autoscaler.models.replica import ServiceReplica
        from apps.deployments.services.node_scorer import NodeScorer
        from apps.deployments.services.spawning_service import SpawningService

        spawned = 0
        try:
            spawner = SpawningService()
            remaining = rec.scale_up_by

            # Priority 1: try local spawn directly (spawn_local checks
            # capacity internally). No separate _check_local_capacity
            # call — that would create a TOCTOU race where capacity
            # passes but spawn_local fails, skipping local entirely.
            local_ok = True

            while spawned < remaining and local_ok:
                replica = ServiceReplica.objects.create(
                    service=self.service, node=None,
                    spawn_reason=rec.reason, status='SPAWNING',
                )
                try:
                    spawner.spawn_local(self.service, replica)
                    spawned += 1
                except Exception as exc:
                    logger.warning("Local spawn failed for %s: %s", self.service.name, exc)
                    try:
                        spawner.destroy(replica)
                    except Exception as exc:
                        logger.debug("Destroy replica after local spawn failure: %s", exc)
                    replica.status = 'DESTROYED'
                    replica.save(update_fields=['status'])
                    local_ok = False

            if spawned >= remaining:
                self._record_scale()
                return ScaleResult(rec, applied=True, spawned=spawned)

            # Priority 2: remote nodes
            candidates = ManagedServer.objects.filter(
                status=ManagedServer.Status.ONLINE,
                allow_user_workloads=True,
            ).exclude(is_primary=True)
            if not candidates.exists():
                logger.warning("No remote nodes for scaling %s", self.service.name)
                if spawned > 0:
                    self._record_scale()
                    return ScaleResult(rec, applied=True, spawned=spawned)
                return ScaleResult(rec, applied=False,
                                   error='No remote nodes available')

            scorer = NodeScorer()
            scores = scorer.score(candidates)
            for node, score, _resources in scores:
                if spawned >= remaining:
                    break
                if score < 20:
                    continue
                replica = ServiceReplica.objects.create(
                    service=self.service, node=node,
                    spawn_reason=rec.reason, status='SPAWNING',
                )
                try:
                    spawner.spawn(self.service, node, replica)
                    spawned += 1
                    logger.info("Auto-scaled %s: spawned on %s", self.service.name, node.name)
                except Exception as exc:
                    logger.warning("Remote spawn failed for %s on %s: %s",
                                   self.service.name, node.name, exc)
                    try:
                        spawner.destroy(replica)
                    except Exception as exc:
                        logger.debug("Destroy replica after remote spawn failure: %s", exc)
                    replica.status = 'DESTROYED'
                    replica.save(update_fields=['status'])
            if spawned > 0:
                self._record_scale()
                return ScaleResult(rec, applied=True, spawned=spawned)
            return ScaleResult(rec, applied=False, error='All spawns failed')
        except Exception as exc:
            logger.warning("Reconciler.scale_up failed for %s: %s", self.service.name, exc)
            return ScaleResult(rec, applied=False, error=str(exc))

    # ── Scale down ───────────────────────────────────────────────────────────
    def _scale_down(self, rec: Recommendation) -> ScaleResult:
        from apps.autoscaler.models.replica import ServiceReplica
        from apps.deployments.services.spawning_service import SpawningService
        destroyed = 0
        target = max(rec.scale_down_by, 1)
        try:
            spawner = SpawningService()
            replicas = ServiceReplica.objects.filter(
                service=self.service, status='RUNNING',
            ).order_by('created_at')[:target]
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
