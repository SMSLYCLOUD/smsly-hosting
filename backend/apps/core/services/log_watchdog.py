# pylint: disable=invalid-name
"""
Log-error watchdog: catch runtime failures liveness probes miss.

A container can answer /health while rotting inside (swallowed import
errors, skipped pools, wedged boot tasks that never raise). This task
scans recent container logs for error bursts and escalates:

- window with >= LOGWATCH_ERROR_LINES error lines -> consecutive counter +1
- counter reaching LOGWATCH_CONSECUTIVE_WINDOWS -> service flagged
  ``needs_manual_intervention`` + health alert dispatched
- clean window resets the counter

Deliberately NEVER auto-restarts or auto-rolls-back: log noise is not
proof, and the existing deployment-failure engine owns rollback. The
flag surfaces in the dashboard/services API for operators (and blocks
the auto-restart path from treating the service as fine).
"""
from __future__ import annotations

import logging
import re
import time

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK
from apps.deployments.models import Deployment, Service

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(__import__('os').environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


LOGWATCH_INTERVAL_SECONDS = 300
LOGWATCH_TAIL_LINES = 200
LOGWATCH_ERROR_LINES = _env_int("LOGWATCH_ERROR_LINES", 10, minimum=1)
LOGWATCH_CONSECUTIVE_WINDOWS = _env_int("LOGWATCH_CONSECUTIVE_WINDOWS", 3, minimum=1)
LOGWATCH_LOCK_SECONDS = 240

_ERROR_RE = re.compile(r'Traceback|\b(ERROR|CRITICAL|FATAL)\b')


def count_log_errors(text: str) -> int:
    """Count error-indicating lines (pure function, unit-tested)."""
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if _ERROR_RE.search(line))


def _counter_key(service_id: str) -> str:
    return f"logwatch:bad:{service_id}"


def _deploy_in_flight(service_id: str) -> bool:
    return Deployment.objects.filter(
        service_id=service_id,
        status__in=[
            Deployment.Status.QUEUED,
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        ],
    ).exists()


@shared_task(
    bind=True,
    queue="celery",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def watch_log_errors_task(self) -> None:
    """Scan ACTIVE services' recent logs for error bursts (overlap-locked)."""
    from celery.exceptions import SoftTimeLimitExceeded

    from apps.core.services.health_monitor import (
        _dispatch_health_alert,
        _fetch_container_logs,
    )

    lock_key = "logwatch:run:lock"
    if not cache.add(lock_key, True, timeout=LOGWATCH_LOCK_SECONDS):
        logger.warning("Log watchdog: previous run still active, skipping overlap")
        return
    try:
        deadline = time.monotonic() + max(30, TASK_TIME_LIMIT_QUICK[0] - 20)
        services = Service.objects.filter(status="ACTIVE").only("id", "name", "health_status")
        flagged = 0
        for service in services:
            if time.monotonic() >= deadline:
                logger.warning("Log watchdog: budget exhausted, deferring rest")
                break
            try:
                if _deploy_in_flight(str(service.id)):
                    continue
                errors = count_log_errors(_fetch_container_logs(service))
                key = _counter_key(str(service.id))
                if errors >= LOGWATCH_ERROR_LINES:
                    bad = (cache.get(key) or 0) + 1
                    cache.set(key, bad, timeout=3600)
                    logger.warning(
                        "Log watchdog: %s window errors=%d consecutive=%d",
                        service.name, errors, bad,
                    )
                    if bad >= LOGWATCH_CONSECUTIVE_WINDOWS and service.health_status != "needs_manual_intervention":
                        service.health_status = "needs_manual_intervention"
                        service.save(update_fields=["health_status", "updated_at"])
                        _dispatch_health_alert(
                            service, "needs_manual_intervention",
                            f"{errors} error lines in recent logs for "
                            f"{bad} consecutive checks; error burst while "
                            "liveness passes — investigate runtime failures",
                        )
                        flagged += 1
                else:
                    cache.delete(key)
            except SoftTimeLimitExceeded:
                logger.warning("Log watchdog: soft time limit hit, stopping run")
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("Log watchdog failed for %s: %s", service.name, exc)
        logger.info("Log watchdog done flagged=%d", flagged)
    finally:
        cache.delete(lock_key)
