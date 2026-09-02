"""Auto-recovery for stale EcosystemPlans (ghost-task unblocker).

ROOT CAUSE (2026-09-02 incident): an ecosystem deploy plan was stuck in
'deploying' for 4 days because its Celery task was a GHOST — the worker
lost the message (result backend shows PENDING forever). The
ecosystem_scan view's concurrent-scan guard rejects every new request
with 429 while ANY plan for the user has status 'scanning' or
'deploying', so the operator was completely locked out of ecosystem
features with no way to recover from the UI.

Recovery rule: any plan in scanning/deploying older than
ECOSYSTEM_PLAN_STALE_MINUTES (default 30) is marked failed. The scan
and deploy tasks themselves are idempotent — re-running them on the
same plan creates a fresh Deployment row, so clearing the stale flag
is always safe (worst case: the old task eventually runs and its
result is superseded).

Registered in celery.py beat_schedule every 10 minutes.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Plans younger than this are presumed genuinely still running.
ECOSYSTEM_PLAN_STALE_MINUTES = 30


@shared_task(
    bind=True,
    name="apps.deployments.tasks.recover_stale_ecosystem_plans",
    soft_time_limit=60,
    time_limit=90,
)
def recover_stale_ecosystem_plans(self):
    """Beat task (10m): clear ghost scanning/deploying EcosystemPlans."""
    from apps.deployments.models.ecosystem import EcosystemPlan

    threshold = timezone.now() - timedelta(minutes=ECOSYSTEM_PLAN_STALE_MINUTES)
    stale = EcosystemPlan.objects.filter(
        status__in=["scanning", "deploying"],
        created_at__lt=threshold,
    )

    recovered = 0
    for plan in stale:
        old_status = plan.status
        age_minutes = int((timezone.now() - plan.created_at).total_seconds() / 60)
        plan.status = "failed"
        plan.error_message = (
            f"Auto-recovered: plan was stuck in '{old_status}' for "
            f"{age_minutes // 60}h{age_minutes % 60}m — Celery task was "
            f"a ghost (lost message). Cleared by recover_stale_ecosystem_plans "
            f"at {timezone.now().isoformat()}. Safe to re-run the scan/deploy."
        )
        plan.save(update_fields=["status", "error_message", "updated_at"])
        recovered += 1
        logger.warning(
            "Recovered stale EcosystemPlan %s (was %s, %d min old)",
            plan.id, old_status, age_minutes,
        )

    if recovered:
        logger.info("recover_stale_ecosystem_plans: cleared %d ghost plan(s)", recovered)
        return {"status": "ok", "recovered": recovered}
    return {"status": "ok", "recovered": 0}
