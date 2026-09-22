# pylint: disable=invalid-name
"""Bulk power tasks: async stop/start/restart sweeps + auto-off timer."""
from __future__ import annotations

import logging

from celery import shared_task
from django.core.cache import cache

from apps.deployments.constants import TASK_TIME_LIMIT_LONG

logger = logging.getLogger(__name__)

__all__ = [
    'bulk_power_task',
    'schedule_auto_off',
    'pending_auto_off',
    'cancel_auto_off',
    'AUTO_OFF_CACHE_KEY',
]

AUTO_OFF_CACHE_KEY = "power:auto-off"


@shared_task(
    bind=True,
    queue="celery",
    soft_time_limit=TASK_TIME_LIMIT_LONG[0],
    time_limit=TASK_TIME_LIMIT_LONG[1],
    name="apps.deployments.tasks.bulk_power_task",
)
def bulk_power_task(self, op: str, actor: str = "system") -> dict:
    """Run a bulk power op (stop/start/restart) across services."""
    from apps.deployments.services.power import power_all
    try:
        summary = power_all(op, actor=actor, stagger_seconds=3 if op == 'restart' else 0)
    except ValueError as exc:
        logger.error("Bulk power failed: %s", exc)
        return {'op': op, 'error': str(exc)}
    logger.info(
        "Bulk power %s done=%d failed=%d skipped=%d",
        op, summary['done_count'], summary['failed_count'], summary['skipped_count'],
    )
    return summary


def schedule_auto_off(seconds_from_now: int, actor: str):
    """Schedule a bulk stop ETA-seconds out; returns {task_id, fires_at}."""
    from datetime import timedelta
    from django.utils import timezone
    seconds_from_now = max(60, min(int(seconds_from_now), 86400))
    fires_at = timezone.now() + timedelta(seconds=seconds_from_now)
    async_result = bulk_power_task.apply_async(args=['stop', actor], eta=fires_at)
    info = {
        'task_id': async_result.id,
        'fires_at': fires_at.isoformat(),
        'actor': actor,
    }
    cache.set(AUTO_OFF_CACHE_KEY, info, timeout=seconds_from_now + 300)
    return info


def pending_auto_off():
    """Pending auto-off timer info, or None."""
    try:
        info = cache.get(AUTO_OFF_CACHE_KEY)
        return dict(info) if isinstance(info, dict) else None
    except Exception:
        return None


def cancel_auto_off():
    """Revoke the pending auto-off timer; returns True when one existed."""
    from celery import current_app
    info = pending_auto_off()
    if not info:
        return False
    try:
        current_app.control.revoke(info.get('task_id'), terminate=False)
    except Exception as exc:
        logger.debug("Auto-off revoke failed: %s", exc)
    cache.delete(AUTO_OFF_CACHE_KEY)
    return True
