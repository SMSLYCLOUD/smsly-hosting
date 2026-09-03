"""Auto-recovery for stale ServerTransfers (ghost-task unblocker).

Same pattern as the EcosystemPlan ghost-plan fix: a transfer whose
Celery task died (worker restart, OOM kill, ghost message) stays in
an ACTIVE status forever, returning 409 on every new transfer to that
target and locking the operator out of the transfer UI.

Recovery rule: any transfer in an active status (PREPARING through
VERIFYING) older than 2 hours is auto-failed. Transfers should finish
in minutes; only true ghosts survive past 2 hours.

Registered in celery.py beat_schedule every 15 minutes.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging(__name__)

TRANSFER_STALE_HOURS = 2
ACTIVE_STATUSES = ['PREPARING', 'UPLOADING', 'RESTORING', 'DNS_CUTOVER', 'VERIFYING']


@shared_task(
    bind=True,
    name="apps.deployments.tasks.recover_stale_transfers",
    soft_time_limit=60,
    time_limit=90,
)
def recover_stale_transfers(self):
    """Beat task (15m): clear ghost ServerTransfers in active states."""
    from apps.deployments.models.transfer import ServerTransfer

    threshold = timezone.now() - timedelta(hours=TRANSFER_STALE_HOURS)
    stale = ServerTransfer.objects.filter(
        status__in=ACTIVE_STATUSES,
        created_at__lt=threshold,
    )

    recovered = 0
    for t in stale:
        old_status = t.status
        age_hours = (timezone.now() - t.created_at).total_seconds() / 3600
        t.status = 'FAILED'
        t.error_message = (
            f"Auto-recovered: transfer was stuck in '{old_status}' for "
            f"{age_hours:.0f} hours — Celery task was a ghost. Cleared by "
            f"recover_stale_transfers at {timezone.now().isoformat()}."
        )
        t.target_ssh_key = ''
        t.target_ssh_password = ''
        t.source_ssh_key = ''
        t.source_ssh_password = ''
        t.save(update_fields=[
            'status', 'error_message',
            'target_ssh_key', 'target_ssh_password',
            'source_ssh_key', 'source_ssh_password',
            'updated_at',
        ])
        recovered += 1
        logger.warning(
            "Recovered stale ServerTransfer %s (was %s, %.1fh old)",
            t.id, old_status, age_hours,
        )

    if recovered:
        logger.info("recover_stale_transfers: cleared %d ghost transfer(s)", recovered)
        return {"status": "ok", "recovered": recovered}
    return {"status": "ok", "recovered": 0}
