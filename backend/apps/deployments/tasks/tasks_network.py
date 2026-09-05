"""Periodic maintenance for project-scoped Docker networks."""

import logging

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="apps.deployments.tasks.cleanup_scoped_networks_task",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def cleanup_scoped_networks_task(self):
    """Remove empty, unreferenced project networks without touching active ones."""
    from django.core.management import call_command
    from io import StringIO

    output = StringIO()
    try:
        call_command("cleanup_scoped_networks", stdout=output, stderr=output)
        message = output.getvalue().strip()
        logger.info("Scoped network cleanup: %s", message)
        return {"status": "ok", "output": message}
    except Exception as exc:
        logger.exception("Scoped network cleanup failed")
        return {"status": "error", "error": str(exc)}
