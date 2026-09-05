"""Auto-recovery for stalled deployments (ghost-worker sweeper).

A deployment whose worker died (crash, OOM, lost message, timed-out wave
that already moved on) sits in a non-terminal state forever: no task owns
it, the wave engine forgot it, and the UI shows "Building..." indefinitely.
Unlike ecosystem plans (recover_stale_ecosystem_plans) and deletions
(recover_stalled_deletions), orphan deployment rows had no sweeper.

A row is stalled when ALL of these hold:
  1. status is non-terminal (QUEUED/REVIEW/BUILDING/..., never
     AWAITING_APPROVAL which waits on a human, never STAGED which the
     auto-promote beat owns, never any terminal state),
  2. created longer ago than STALLED_DEPLOYMENT_AGE_MINUTES (a healthy
     deploy is picked up within minutes, so an hour-old untouched row
     was never claimed),
  3. updated longer ago than STALLED_DEPLOYMENT_IDLE_MINUTES (a live
     worker bumps updated_at on every status transition and log append,
     so silence means no worker),
  4. its service's project has no EcosystemPlan currently 'deploying'
     (the wave engine legitimately parks future-wave rows untouched for
     hours — never steal those).

Stalled rows are marked CANCELLED (never retried — the sweeper cannot
know why the worker died, and blind re-queueing is how retry storms
start). Registered in celery.py beat_schedule every 15 minutes.
"""
import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from apps.deployments.constants import (
    STALLED_DEPLOYMENT_AGE_MINUTES,
    STALLED_DEPLOYMENT_BATCH_SIZE,
    STALLED_DEPLOYMENT_IDLE_MINUTES,
    TASK_TIME_LIMIT_QUICK,
)
from apps.deployments.models import Deployment

logger = logging.getLogger(__name__)


# States a deployment row may rest in while NOBODY owns it. Deliberately
# excludes AWAITING_APPROVAL (human gate — may wait days) and STAGED
# (owned by the auto-promote-staged beat task).
_SWEEPABLE_STATES = frozenset({
    Deployment.Status.QUEUED,
    Deployment.Status.REVIEW,
    Deployment.Status.BUILDING,
    Deployment.Status.BACKUP_RUNNING,
    Deployment.Status.MIGRATION_PLANNING,
    Deployment.Status.MIGRATION_RUNNING,
    Deployment.Status.DEPLOYING,
    Deployment.Status.HEALTH_CHECK,
    Deployment.Status.ROLLING_BACK,
})


def _project_has_live_plan(project_id) -> bool:
    """True when an EcosystemPlan for this project is still deploying.

    The wave engine parks future-wave deployments untouched for hours by
    design — those rows must never be swept. Any DB error fails open
    (assume alive — never cancel on uncertainty).
    """
    if not project_id:
        return False
    try:
        from apps.deployments.models.ecosystem import EcosystemPlan
        return EcosystemPlan.objects.filter(
            project_id=project_id,
            status=EcosystemPlan.Status.DEPLOYING,
        ).exists()
    except Exception as exc:
        logger.debug("Live-plan check failed for project %s: %s", project_id, exc)
        return True


@shared_task(bind=True, name="apps.deployments.tasks.recover_stalled_deployments", soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1])
def recover_stalled_deployments(self):
    """Periodic task: cancel deployments stalled with no live worker."""
    now = timezone.now()
    age_cutoff = now - timedelta(minutes=STALLED_DEPLOYMENT_AGE_MINUTES)
    idle_cutoff = now - timedelta(minutes=STALLED_DEPLOYMENT_IDLE_MINUTES)

    rows = list(Deployment.objects.select_related("service").filter(
        status__in=_SWEEPABLE_STATES,
        created_at__lt=age_cutoff,
        updated_at__lt=idle_cutoff,
    ).order_by("created_at")[:STALLED_DEPLOYMENT_BATCH_SIZE])
    if not rows:
        return {"cancelled": 0}

    cancelled = 0
    skipped_live_plan = 0
    try:
        for dep in rows:
            try:
                project_id = getattr(getattr(dep, "service", None), "project_id", None)
                if _project_has_live_plan(project_id):
                    skipped_live_plan += 1
                    continue
                dep.status = Deployment.Status.CANCELLED
                dep.finished_at = now
                dep.build_logs = (
                    f"{dep.build_logs or ''}"
                    "\n[Recovery] Cancelled: no worker activity for "
                    f"{STALLED_DEPLOYMENT_IDLE_MINUTES}+ minutes "
                    "(recover_stalled_deployments).\n"
                )
                dep.save(update_fields=["status", "finished_at", "build_logs", "updated_at"])
                cancelled += 1
            except Exception as exc:
                logger.warning("Failed to cancel stalled deployment %s: %s", getattr(dep, "id", "?"), exc)
    except SoftTimeLimitExceeded:
        logger.warning("recover_stalled_deployments hit soft time limit after %d cancellations", cancelled)
    logger.info("Cancelled %d stalled deployment(s), skipped %d owned by live plans", cancelled, skipped_live_plan)
    return {"cancelled": cancelled, "skipped_live_plan": skipped_live_plan}
