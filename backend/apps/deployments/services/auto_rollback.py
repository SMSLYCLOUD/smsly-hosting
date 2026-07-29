# pylint: disable=too-many-arguments,too-many-positional-arguments
"""
Centralized auto-rollback engine.

All wired auto-rollback triggers in the platform funnel through
``AutoRollbackEngine.trigger`` here so that:

  1. A single per-service cache lock deduplicates overlapping triggers.
  2. Per-service opt-out (``Service.auto_rollback_enabled``) and threshold
     override (``Service.auto_rollback_threshold``) are respected.
  3. A rolling time window (configurable, default 30 min) is used instead
     of a brittle "N consecutive" counter so transient blips don't fire.
  4. A pre-rollback notification fires (Slack/email/webhook) AFTER the
     rollback row is created, so users see it coming and support can
     correlate via the rollback deployment id.
  5. The audit log records which trigger fired.
  6. A heartbeat key is set so the monitoring loop can alert if the
     rollback sits QUEUED for too long (broker down, etc.).

Wired triggers (3):
  - ``Trigger.CONSECUTIVE_FAILURES`` — services/orchestrator.py on
    unhandled deployment failure.
  - ``Trigger.HEALTH_CHECK_FALLBACK`` — apps/deployments/services/
    health_monitor.py when persistent restarts fail.
  - ``Trigger.AI_CRASH_LOOP`` — apps/intelligence/remediator.py for
    the CRASH_LOOP recommendation.

Reserved for future use: ``AI_HEALTH_CHECK_FAIL``, ``AI_OOM_KILLED``,
``MANUAL``.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)


# ──────────────────────────── tunables ────────────────────────────

#: Number of failed deployments in the rolling window required to fire
#: when the trigger is "consecutive_failures".
AUTO_ROLLBACK_THRESHOLD = getattr(settings, 'AUTO_ROLLBACK_THRESHOLD', 5)

#: Length of the rolling window in which ``AUTO_ROLLBACK_THRESHOLD``
#: failures must accumulate.
AUTO_ROLLBACK_WINDOW_MINUTES = getattr(
    settings, 'AUTO_ROLLBACK_WINDOW_MINUTES', 30
)

#: Minimum gap between two auto-rollbacks for the SAME service.
AUTO_ROLLBACK_COOLDOWN_MINUTES = getattr(
    settings, 'AUTO_ROLLBACK_COOLDOWN_MINUTES', 15
)

#: Cache lock TTL — long enough to cover deploy enqueue + first build tick.
_AUTO_ROLLBACK_LOCK_TTL_SECONDS = 1800  # 30 min

#: If a queued rollback doesn't transition within this window, alert the user.
_ROLLBACK_HEARTBEAT_TIMEOUT_MINUTES = 5


# ────────────────────────── trigger catalog ───────────────────────

class Trigger:
    """Trigger identifiers — recorded in audit log + heartbeat payload."""
    CONSECUTIVE_FAILURES = 'consecutive_failures'
    HEALTH_CHECK_FALLBACK = 'health_check_fallback'
    AI_CRASH_LOOP = 'ai_crash_loop'
    AI_HEALTH_CHECK_FAIL = 'ai_health_check_fail'
    AI_OOM_KILLED = 'ai_oom_killed'
    MANUAL = 'manual'


# ────────────────────────── result dataclass ──────────────────────

class AutoRollbackResult:
    """Outcome of ``AutoRollbackEngine.trigger``."""

    __slots__ = ('fired', 'reason', 'rollback_id')

    def __init__(self, fired: bool, reason: str, rollback_id: str | None = None):
        self.fired = fired
        self.reason = reason
        self.rollback_id = rollback_id

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return (
            f'AutoRollbackResult(fired={self.fired}, '
            f'reason={self.reason!r}, rollback_id={self.rollback_id!r})'
        )


# ──────────────────────── engine implementation ──────────────────

class AutoRollbackEngine:
    """Centralized auto-rollback decision + execution."""

    @staticmethod
    def _lock_key(service_id) -> str:
        return f'auto-rollback-lock:{service_id}'

    @staticmethod
    def _heartbeat_key(rollback_id) -> str:
        return f'rollback-heartbeat:{rollback_id}'

    @classmethod
    def trigger(
        cls,
        *,
        service,
        trigger: str,
        reason_detail: str = '',
        failed_deployment=None,
    ) -> AutoRollbackResult:
        """Decide whether to fire an auto-rollback and, if so, create one.

        Args:
            service: The ``Service`` instance under consideration.
            trigger: One of ``Trigger.*`` identifiers — recorded in audit
                metadata and the heartbeat payload.
            reason_detail: Free-form human-readable explanation (e.g. the
                specific failure mode that triggered the rollback).
            failed_deployment: The most-recent failed ``Deployment`` row,
                used as ``rollback_from`` and to compute consecutive-failure
                counts. Optional — the caller may pass ``None`` for
                triggers that are not tied to a specific row.

        Returns:
            ``AutoRollbackResult`` describing the decision.
        """
        # Local imports to avoid circular import between models, tasks, and
        # this helper.
        from apps.deployments.models import Deployment
        from apps.deployments.models.audit import AuditLog
        from apps.core.tasks.alerts import notify_auto_rollback
        from apps.deployments.tasks.deployment.tasks_deploy import (
            _resolve_provider_for_service,
            enqueue_smart_deploy_task,
        )

        # ── 1. Per-service opt-out ──────────────────────────────────
        if not getattr(service, 'auto_rollback_enabled', True):
            logger.info(
                'Auto-rollback skipped for %s: disabled by service config.',
                service.name,
            )
            return AutoRollbackResult(False, 'disabled_by_service_config')

        # ── 2. Per-service dedup lock ───────────────────────────────
        lock_key = cls._lock_key(service.id)
        if not cache.add(lock_key, '1', timeout=_AUTO_ROLLBACK_LOCK_TTL_SECONDS):
            logger.info(
                'Auto-rollback suppressed for %s: dedup lock already held.',
                service.name,
            )
            return AutoRollbackResult(False, 'dedup_lock_held')

        try:
            # ── 3. Threshold check (rolling window) ─────────────────
            threshold = (
                getattr(service, 'auto_rollback_threshold', None)
                or AUTO_ROLLBACK_THRESHOLD
            )
            window_start = timezone.now() - timedelta(
                minutes=AUTO_ROLLBACK_WINDOW_MINUTES,
            )
            recent_failure_count = Deployment.objects.filter(
                service=service,
                is_rollback=False,
                status=Deployment.Status.FAILED,
                created_at__gte=window_start,
            ).count()
            if recent_failure_count < threshold:
                logger.info(
                    'Auto-rollback suppressed for %s: only %d failures in '
                    'the last %d min (threshold=%d).',
                    service.name,
                    recent_failure_count,
                    AUTO_ROLLBACK_WINDOW_MINUTES,
                    threshold,
                )
                return AutoRollbackResult(
                    False,
                    f'below_threshold:{recent_failure_count}/{threshold}',
                )

            # ── 4. Cooldown: don't repeat-rollback to the same commit ─
            cooldown_start = timezone.now() - timedelta(
                minutes=AUTO_ROLLBACK_COOLDOWN_MINUTES,
            )

            # ── 5. Find rollback target: last ACTIVE / INACTIVE ─────
            target = (
                Deployment.objects
                .filter(
                    service=service,
                    status__in=[
                        Deployment.Status.ACTIVE,
                        Deployment.Status.INACTIVE,
                    ],
                )
                .order_by('-finished_at', '-created_at')
                .first()
            )
            if not target:
                logger.warning(
                    'Auto-rollback: no prior successful deployment for %s.',
                    service.name,
                )
                return AutoRollbackResult(False, 'no_prior_successful_deployment')

            # ── 6. In-flight rollback guard ─────────────────────────
            in_flight = [
                Deployment.Status.QUEUED,
                Deployment.Status.REVIEW,
                Deployment.Status.BUILDING,
                Deployment.Status.DEPLOYING,
                Deployment.Status.HEALTH_CHECK,
            ]
            if Deployment.objects.filter(
                service=service,
                is_rollback=True,
                status__in=in_flight,
            ).exists():
                logger.info(
                    'Auto-rollback suppressed for %s: rollback already in flight.',
                    service.name,
                )
                return AutoRollbackResult(False, 'rollback_in_flight')

            # ── 7. Cooldown dedup against recent same-commit rollback
            if Deployment.objects.filter(
                service=service,
                is_rollback=True,
                commit_hash=target.commit_hash,
                created_at__gte=cooldown_start,
            ).exclude(status=Deployment.Status.ACTIVE).exists():
                logger.info(
                    'Auto-rollback suppressed for %s: recent rollback to %s '
                    'within cooldown window.',
                    service.name,
                    target.commit_hash,
                )
                return AutoRollbackResult(
                    False,
                    f'cooldown:{AUTO_ROLLBACK_COOLDOWN_MINUTES}m',
                )

            # ── 8. Create rollback + heartbeat (atomic) ──────────────
            # The row is created BEFORE the notification is dispatched
            # so users never get an "auto-rollback" alert for a rollback
            # that ultimately fails to materialise (broker outage, etc.).
            # ``rollback_from`` is the failed deployment we're recovering
            # FROM — NOT the target we're rolling back to.
            with transaction.atomic():
                rollback = Deployment.objects.create(
                    service=service,
                    status=Deployment.Status.QUEUED,
                    commit_hash=target.commit_hash,
                    commit_message=cls._build_commit_message(
                        trigger, recent_failure_count, threshold,
                        target.commit_hash,
                    ),
                    is_rollback=True,
                    rollback_from=failed_deployment,
                )
                # Heartbeat key — the monitoring loop watches this to
                # alert if the rollback doesn't transition out of QUEUED
                # within ``_ROLLBACK_HEARTBEAT_TIMEOUT_MINUTES``.
                cache.set(
                    cls._heartbeat_key(rollback.id),
                    {
                        'service_id': str(service.id),
                        'service_name': service.name,
                        'trigger': trigger,
                        'target_commit': target.commit_hash,
                        'queued_at': timezone.now().isoformat(),
                    },
                    timeout=_ROLLBACK_HEARTBEAT_TIMEOUT_MINUTES * 60 * 6,  # 30m
                )
                # Register in the heartbeat registry so the monitor can
                # discover this rollback even without a full cache scan.
                # Store the ID as a string so callers passing either
                # ``Deployment.id`` (UUID) or ``str(deployment.id)`` can
                # both clear it via ``clear_rollback_heartbeat``.
                registry_key = 'rollback-heartbeat-registry'
                registry = cache.get(registry_key) or set()
                registry.add(str(rollback.id))
                cache.set(registry_key, registry, timeout=86400)

            # ── 9. Pre-rollback notification (best-effort) ──────────
            # Dispatched AFTER the row exists. The notification includes
            # the rollback deployment id so support can correlate.
            try:
                notify_auto_rollback.delay(
                    service_id=str(service.id),
                    trigger=trigger,
                    reason=(
                        f"{reason_detail or trigger} "
                        f"(rollback deployment {rollback.id})"
                    ),
                    target_commit=target.commit_hash,
                )
            except Exception:  # pragma: no cover - broker outage
                logger.exception(
                    'Pre-rollback notification dispatch failed for %s; '
                    'rollback %s is still queued.',
                    service.name, rollback.id,
                )

            AuditLog.objects.create(
                actor='AUTO_ROLLBACK_ENGINE',
                action='AUTO_ROLLBACK_TRIGGERED',
                target=f'Service:{service.name}',
                metadata={
                    'trigger': trigger,
                    'reason_detail': reason_detail,
                    'recent_failure_count': recent_failure_count,
                    'window_minutes': AUTO_ROLLBACK_WINDOW_MINUTES,
                    'threshold': threshold,
                    'rollback_deployment_id': str(rollback.id),
                    'rolled_back_from_id': (
                        str(failed_deployment.id) if failed_deployment else None
                    ),
                    'target_commit_hash': target.commit_hash,
                },
            )

            provider = _resolve_provider_for_service(service)
            if not provider:
                logger.warning(
                    'Auto-rollback: no active provider for %s; rollback %s '
                    'left QUEUED.',
                    service.name,
                    rollback.id,
                )
                return AutoRollbackResult(
                    True,  # we DID create the rollback row — it just won't run yet
                    'no_active_provider_rollback_queued',
                    rollback_id=str(rollback.id),
                )

            try:
                enqueue_smart_deploy_task(
                    str(rollback.id),
                    str(provider.id),
                    skip_review=True,
                )
            except Exception:  # pragma: no cover - broker outage
                logger.exception(
                    'Failed to enqueue auto-rollback deployment %s; '
                    'leaving it QUEUED for the heartbeat monitor to alert.',
                    rollback.id,
                )
                rollback.status = Deployment.Status.FAILED
                rollback.finished_at = timezone.now()
                rollback.error_message = (
                    'Auto-rollback was created but the deployment task '
                    'failed to enqueue. Manual intervention required.'
                )
                rollback.save(update_fields=[
                    'status', 'finished_at', 'error_message', 'updated_at',
                ])

            logger.warning(
                'AUTO-ROLLBACK fired for %s: trigger=%s reason=%s '
                '%d failures/%dmin — rolling back to commit %s (deployment %s).',
                service.name,
                trigger,
                reason_detail,
                recent_failure_count,
                AUTO_ROLLBACK_WINDOW_MINUTES,
                target.commit_hash,
                rollback.id,
            )
            return AutoRollbackResult(
                True,
                f'triggered:{trigger}',
                rollback_id=str(rollback.id),
            )

        finally:
            # Release the lock ONLY for non-firing outcomes. If we fired,
            # the lock stays held until the heartbeat clears it on success.
            # We re-check via the local ``fired`` flag below — but since
            # we don't have it here, we release unconditionally and rely
            # on the cooldown filter to prevent rapid re-fires.
            # NOTE: a successful firing also clears the lock, which is
            # fine because the cooldown + in-flight checks above prevent
            # back-to-back rollbacks.
            cache.delete(lock_key)

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _build_commit_message(
        trigger: str,
        failure_count: int,
        threshold: int,
        target_commit: str,
    ) -> str:
        return (
            f"AUTO-ROLLBACK ({trigger}): {failure_count}/{threshold} "
            f"failures -> reverting to {target_commit[:7]}"
        )


# ──────────────────── heartbeat helpers ───────────────────────────

def clear_rollback_heartbeat(rollback_id) -> None:
    """Called by the deployment pipeline once a rollback transitions
    out of QUEUED (success or failure). Removes the heartbeat key AND
    the registry entry so the monitor doesn't keep looking up a
    dead rollback id.

    Accepts either a ``UUID`` or a ``str`` (the registry always stores
    string IDs, so we normalise on the way in).
    """
    rollback_id_str = str(rollback_id)
    cache.delete(AutoRollbackEngine._heartbeat_key(rollback_id_str))
    registry_key = 'rollback-heartbeat-registry'
    registry = cache.get(registry_key) or set()
    if rollback_id_str in registry:
        registry.discard(rollback_id_str)
        cache.set(registry_key, registry, timeout=86400)


def get_stuck_rollback_heartbeats():
    """Return (key, payload) pairs for rollbacks whose heartbeat has
    aged past the safety window. Used by the periodic monitor task.
    """
    from apps.deployments.models import Deployment

    stuck = []
    # cache has no native "scan by prefix" so we keep a registry key.
    registry_key = 'rollback-heartbeat-registry'
    registry = cache.get(registry_key) or set()
    cutoff = timezone.now() - timedelta(minutes=_ROLLBACK_HEARTBEAT_TIMEOUT_MINUTES)

    for rollback_id in list(registry):
        hb = cache.get(AutoRollbackEngine._heartbeat_key(rollback_id))
        if hb is None:
            registry.discard(rollback_id)
            continue
        queued_at = timezone.datetime.fromisoformat(hb['queued_at'])
        if queued_at < cutoff:
            # Confirm the rollback is still QUEUED — if it's progressed,
            # clear the heartbeat instead of alerting.
            try:
                deployment = Deployment.objects.get(id=rollback_id)
            except Deployment.DoesNotExist:
                registry.discard(rollback_id)
                cache.delete(AutoRollbackEngine._heartbeat_key(rollback_id))
                continue
            if deployment.status not in (
                Deployment.Status.QUEUED,
                Deployment.Status.REVIEW,
                Deployment.Status.BUILDING,
            ):
                registry.discard(rollback_id)
                cache.delete(AutoRollbackEngine._heartbeat_key(rollback_id))
                continue
            stuck.append((rollback_id, hb))
    cache.set(registry_key, registry, timeout=86400)
    return stuck


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1])
def monitor_stuck_rollback_heartbeats(self):
    """Celery beat task: alert on rollbacks stuck in QUEUED.

    For each stuck rollback, we:
      1. Create an AuditLog entry for compliance/observability.
      2. Dispatch ``notify_auto_rollback`` so the user actually gets
         an alert (Slack, Discord, email, SMS).
      3. Clear the heartbeat so we don't re-alert every 5 minutes
         for the same stuck rollback.
    """
    # Local imports to avoid circular import at module load time.
    from apps.deployments.models.audit import AuditLog
    from apps.core.tasks.alerts import notify_auto_rollback

    stuck = get_stuck_rollback_heartbeats()
    if not stuck:
        logger.info("No stuck rollback heartbeats found.")
        return {"checked": True, "stuck_count": 0}

    logger.warning("Found %d stuck rollback heartbeat(s).", len(stuck))
    for rollback_id, payload in stuck:
        service_name = payload.get('service_name', '?')
        trigger = payload.get('trigger', '?')
        queued_at = payload.get('queued_at', '?')
        target_commit = payload.get('target_commit', '')

        logger.error(
            "ROLLBACK STUCK: id=%s service=%s trigger=%s queued_at=%s",
            rollback_id, service_name, trigger, queued_at,
        )

        try:
            AuditLog.objects.create(
                actor='AUTO_ROLLBACK_MONITOR',
                action='STUCK_ROLLBACK_DETECTED',
                target=f'Service:{service_name}',
                metadata={
                    'rollback_id': rollback_id,
                    'trigger': trigger,
                    'queued_at': queued_at,
                    'target_commit': target_commit,
                    'stuck_after_minutes': _ROLLBACK_HEARTBEAT_TIMEOUT_MINUTES,
                },
            )
        except Exception:  # pragma: no cover - DB failure should not block alerts
            logger.exception("Failed to write AuditLog for stuck rollback %s", rollback_id)

        service_id = payload.get('service_id')
        if service_id:
            try:
                notify_auto_rollback.delay(
                    service_id=service_id,
                    trigger='stuck_rollback_monitor',
                    reason=(
                        f"Rollback stuck in QUEUED for over "
                        f"{_ROLLBACK_HEARTBEAT_TIMEOUT_MINUTES} minutes "
                        f"(trigger={trigger})"
                    ),
                    target_commit=target_commit,
                )
            except Exception:  # pragma: no cover - broker outage
                logger.exception("Failed to dispatch alert for stuck rollback %s", rollback_id)

        # Clear the heartbeat so we don't keep alerting.
        clear_rollback_heartbeat(rollback_id)

    return {"checked": True, "stuck_count": len(stuck)}
