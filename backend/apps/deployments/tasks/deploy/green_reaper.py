# pylint: disable=invalid-name
"""
Green reaper: stop resource-burning containers of dead rollouts, fast.

The failure handler removes green/blue containers when it runs, but any
failure that bypasses it (wedged boot, crashed worker mid-flow, stuck
STAGED green) leaves containers "Up (unhealthy)" burning CPU/RAM until
the 30-minute orphan sweep — which explicitly skips referenced greens.
This task closes that gap every few minutes:

- terminal-failure deployments (FAILED family + CANCELLED): stop green
  and container ids that are still RUNNING and not anyone's ACTIVE blue.
- STAGED deployments whose green is explicitly unhealthy/starting for
  longer than the soak: stop it (a green that can't start in 45 min is
  not going to promote).

STOP, never remove: logs stay debuggable and the orphan sweep still owns
removal. Never touches ACTIVE rows' containers. Never writes deployment
rows (row saves would retrigger status-recompute signals).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK
from apps.deployments.models import Deployment

logger = logging.getLogger(__name__)

__all__ = ['reap_failed_greens_task']


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


REAPER_INTERVAL_SECONDS = 180
REAPER_LOCK_SECONDS = 150
REAPER_MAX_ROWS = _env_int("GREEN_REAPER_MAX_ROWS", 200, minimum=1)
REAPER_LOOKBACK_DAYS = _env_int("GREEN_REAPER_LOOKBACK_DAYS", 7, minimum=1)
REAPER_STAGED_SOAK_MINUTES = _env_int("GREEN_REAPER_STAGED_SOAK_MINUTES", 45, minimum=5)

TERMINAL_STATUSES = (
    Deployment.Status.FAILED,
    Deployment.Status.BUILD_FAILED,
    Deployment.Status.BACKUP_FAILED,
    Deployment.Status.MIGRATION_FAILED,
    Deployment.Status.HEALTH_CHECK_FAILED,
    Deployment.Status.CANCELLED,
)


def _container_running(container_id: str):
    """Return the container object when RUNNING, else None (seam for tests)."""
    try:
        from apps.deployments.services.container_runtime import ContainerRuntime
        container = ContainerRuntime().get_container(container_id)
        container.reload()
        state = str((container.attrs.get('State', {}) or {}).get('Status') or '').lower()
        health = str((container.attrs.get('State', {}).get('Health', {}) or {}).get('Status') or '').lower()
        started = (container.attrs.get('State', {}) or {}).get('StartedAt') or ''
        if state == 'running':
            return container, health, started
        return None, health, started
    except Exception:
        return None, '', ''


def _started_minutes_ago(started_at: str) -> float | None:
    try:
        from django.utils.dateparse import parse_datetime
        started = parse_datetime(started_at)
        if started is None:
            return None
        if timezone.is_naive(started):
            started = timezone.make_aware(started)
        return (timezone.now() - started).total_seconds() / 60.0
    except Exception:
        return None


@shared_task(
    bind=True,
    queue="celery",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
    name="apps.deployments.tasks.reap_failed_greens_task",
)
def reap_failed_greens_task(self) -> None:
    """Stop running containers of dead rollouts (overlap-locked, budgeted)."""
    from celery.exceptions import SoftTimeLimitExceeded

    lock_key = "green-reaper:run:lock"
    if not cache.add(lock_key, True, timeout=REAPER_LOCK_SECONDS):
        logger.warning("Green reaper: previous run still active, skipping overlap")
        return
    try:
        deadline = time.monotonic() + max(30, TASK_TIME_LIMIT_QUICK[0] - 20)
        since = timezone.now() - timedelta(days=REAPER_LOOKBACK_DAYS)

        # Blue safety: never touch anything referenced as an ACTIVE row's
        # container (live traffic), regardless of other rows' states.
        live_ids = set(
            Deployment.objects.filter(status=Deployment.Status.ACTIVE)
            .exclude(container_id__isnull=True).exclude(container_id='')
            .values_list('container_id', flat=True)
        ) | set(
            Deployment.objects.filter(status=Deployment.Status.ACTIVE)
            .exclude(green_container_id__isnull=True).exclude(green_container_id='')
            .values_list('green_container_id', flat=True)
        )

        rows = (
            Deployment.objects.filter(
                status__in=[*TERMINAL_STATUSES, Deployment.Status.STAGED],
                created_at__gte=since,
            )
            .only('id', 'status', 'container_id', 'green_container_id', 'service_id', 'created_at')
            .order_by('-created_at')[:REAPER_MAX_ROWS]
        )
        stopped = 0
        for dep in rows:
            if time.monotonic() >= deadline:
                logger.warning("Green reaper: budget exhausted, deferring rest")
                break
            try:
                candidates = {dep.green_container_id, dep.container_id} - {None, ''}
                candidates -= live_ids
                if not candidates:
                    continue
                if dep.status == Deployment.Status.STAGED:
                    # Only reap staged greens that are verifiably wedged:
                    # explicitly unhealthy (or starting far too long).
                    # A healthy running container of a STAGED row is a
                    # valid promote candidate — never touch it.
                    to_stop = set()
                    for cid in list(candidates):
                        running, health, started = _container_running(cid)
                        if running is None:
                            continue
                        age = _started_minutes_ago(started) or 0
                        if health in ('unhealthy', 'starting') and age >= REAPER_STAGED_SOAK_MINUTES:
                            to_stop.add(cid)
                    candidates = to_stop
                    if not candidates:
                        continue
                from apps.deployments.services.container_runtime import ContainerRuntime
                runtime = ContainerRuntime()
                for cid in candidates:
                    try:
                        runtime.stop_container(cid)
                        stopped += 1
                        logger.warning(
                            "Green reaper: stopped %s of %s deployment %s",
                            cid[:12], dep.status, dep.id,
                        )
                    except Exception as exc:
                        logger.debug("Green reaper: stop failed for %s: %s", cid[:12], exc)
            except SoftTimeLimitExceeded:
                logger.warning("Green reaper: soft time limit hit, stopping run")
                break
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("Green reaper failed for deployment %s: %s", dep.id, exc)
        logger.info("Green reaper done stopped=%d", stopped)
    finally:
        cache.delete(lock_key)
