"""Autoscaler periodic Celery task — populates history cache."""
from __future__ import annotations

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK


@shared_task(soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1])
def autoscaler_collect_stats() -> None:
    """Run inline autoscaler check to populate stats/history in Redis cache."""
    from apps.autoscaler.views import _run_autoscaler_check
    _run_autoscaler_check()
