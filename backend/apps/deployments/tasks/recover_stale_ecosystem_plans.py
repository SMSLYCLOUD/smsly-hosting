"""Auto-recovery for stale EcosystemPlans (ghost-task unblocker).

ROOT CAUSE (2026-09-02 incident): an ecosystem deploy plan was stuck in
'deploying' for 4 days because its Celery task was a GHOST — the worker
lost the message (result backend shows PENDING forever). The
ecosystem_scan view's concurrent-scan guard rejects every new request
with 429 while ANY plan for the user has status 'scanning' or
'deploying', so the operator was completely locked out of ecosystem
features with no way to recover from the UI.

Recovery rule: a plan in scanning/deploying older than
ECOSYSTEM_PLAN_STALE_MINUTES (default 30) is marked failed — UNLESS its
project still has recent deployment activity. The wave engine
(ecosystem_release_wave_task) legitimately chains for hours (30 rechecks
x 30 min) while the plan row itself is never touched, so keying purely
on created_at would auto-fail an actively-deploying plan. A plan is
only "ghost" when BOTH:
  1. the plan row is older than the stale threshold, AND
  2. no deployment in the plan's project has been updated within the
      activity window (ECOSYSTEM_ACTIVITY_MINUTES, default 30).

The scan and deploy tasks themselves are idempotent — re-running them
on the same plan creates a fresh Deployment row, so clearing the stale
flag is always safe (worst case: the old task eventually runs and its
result is superseded).

Registered in celery.py beat_schedule every 10 minutes.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)

# Plans younger than this are presumed genuinely still running.
ECOSYSTEM_PLAN_STALE_MINUTES = 30
# A deployment row touched within this window proves the wave engine
# is still alive — never fail a plan with recent deployment activity.
# 30 min (not 10): a single service iteration can legitimately take
# >10 min — addon provisioning has 60s health-check timeouts per addon
# and AI enrichment backs off through provider 429 storms — with the
# heartbeat only firing at the END of each iteration. A true ghost
# never heartbeats and touches nothing, so it is still cleared.
ECOSYSTEM_ACTIVITY_MINUTES = 30


@shared_task(
    bind=True,
    name="apps.deployments.tasks.recover_stale_ecosystem_plans",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def recover_stale_ecosystem_plans(self):
    """Beat task (10m): clear ghost scanning/deploying EcosystemPlans."""
    from apps.deployments.models import Deployment
    from apps.deployments.models.ecosystem import EcosystemPlan

    now = timezone.now()
    threshold = now - timedelta(minutes=ECOSYSTEM_PLAN_STALE_MINUTES)
    activity_cutoff = now - timedelta(minutes=ECOSYSTEM_ACTIVITY_MINUTES)
    stale = EcosystemPlan.objects.filter(
        status__in=["scanning", "deploying"],
        created_at__lt=threshold,
    )

    recovered = 0
    skipped_alive = 0
    for plan in stale:
        # Freshness check first: the deploy task heartbeats the plan row
        # after each prepared service, and finalization touches it too. A
        # recently-updated plan is alive (possibly mid-preparation with no
        # deployment rows yet) — never treat it as a ghost.
        if plan.updated_at and plan.updated_at >= activity_cutoff:
            skipped_alive += 1
            logger.debug(
                "EcosystemPlan %s is %s but was updated recently — "
                "presumed alive, not recovering",
                plan.id, plan.status,
            )
            continue
        # Activity check: does the plan's project have ANY deployment
        # touched recently? A building/queued deployment bumps updated_at
        # on every status transition and log append, so a live wave
        # chain always looks fresh.
        if plan.project_id:
            # Activity must be scoped to this plan's own ecosystem
            # deployments, otherwise a normal project build can keep a
            # ghost plan alive forever. Match the creation marker as well
            # as the hash: the build pipeline rewrites commit_hash to the
            # real git SHA once cloning starts, so in-flight builds would
            # otherwise be invisible here (2026-09-07 incident).
            from django.db.models import Q
            recent_activity = Deployment.objects.filter(
                service__project_id=plan.project_id,
                updated_at__gte=activity_cutoff,
            ).filter(
                Q(commit_hash="ecosystem-deploy")
                | Q(build_logs__contains="Ecosystem deploy:"),
            ).exists()
            if recent_activity:
                skipped_alive += 1
                logger.debug(
                    "EcosystemPlan %s is %s but has recent ecosystem "
                    "deployment activity — presumed alive, not recovering",
                    plan.id, plan.status,
                )
                continue

        old_status = plan.status
        age_minutes = int((now - plan.created_at).total_seconds() / 60)
        plan.status = "failed"
        plan.error_message = (
            f"Auto-recovered: plan was stuck in '{old_status}' for "
            f"{age_minutes // 60}h{age_minutes % 60}m with no deployment "
            f"activity for {ECOSYSTEM_ACTIVITY_MINUTES} min — Celery task "
            f"was a ghost (lost message). Cleared by "
            f"recover_stale_ecosystem_plans at {now.isoformat()}. "
            f"Safe to re-run the scan/deploy."
        )
        plan.save(update_fields=["status", "error_message", "updated_at"])
        recovered += 1
        logger.warning(
            "Recovered stale EcosystemPlan %s (was %s, %d min old)",
            plan.id, old_status, age_minutes,
        )

    if recovered or skipped_alive:
        logger.info(
            "recover_stale_ecosystem_plans: cleared %d ghost plan(s), "
            "kept %d alive (recent deployment activity)",
            recovered, skipped_alive,
        )
    return {
        "status": "ok",
        "recovered": recovered,
        "kept_alive": skipped_alive,
    }
