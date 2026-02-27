"""Autoscaler periodic Celery task — populates history cache."""
from celery import shared_task


@shared_task
def autoscaler_collect_stats():
    """Run inline autoscaler check to populate stats/history in Redis cache."""
    from apps.autoscaler.views import _run_autoscaler_check
    _run_autoscaler_check()
