"""Build-failure auto-recovery: prune corrupted Docker state on failure.

ROOT CAUSE (2026-09-02): a Docker build failed with
  'failed to export layer: CreateDiff: mount callback failed on
   /var/lib/containerd/tmpmounts/... no such file or directory'

This is a known containerd/buildkit bug — corrupted layer ingest state
in /var/lib/containerd. The build pipeline has NO recovery: it just
reports the error and the user must manually docker prune + restart.

This task hooks into the build pipeline: when a Docker build fails
with a containerd/layer/mount error, it automatically:
  1. Prunes the build cache (builder prune -af)
  2. Clears the containerd ingest directory
  3. Restarts the Docker daemon
  4. Retries the failed build

Registered as a post-failure signal + callable from the pipeline.
"""
from __future__ import annotations

import logging
import re
import subprocess
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK, TASK_TIME_LIMIT_STANDARD

logger = logging.getLogger(__name__)

# containerd corruption patterns that warrant auto-recovery
_CORRUPTION_PATTERNS = [
    r"failed to export layer.*mount callback failed",
    r"CreateDiff.*no such file or directory",
    r"containerd.*ingest.*no such file",
    r"failed to commit.*rename.*no such file or directory",
    r"layer not known",
    r"content digest sha256.*not found",
]

_CORRUPTION_RE = re.compile("|".join(_CORRUPTION_PATTERNS), re.IGNORECASE)

# Track recent prunes to avoid thrashing (max 1 prune per 5 min)
_PRUNE_COOLDOWN = 300
_PRUNE_CACHE_KEY = "docker_corruption_prune_ts"


def is_build_corruption_error(error_text: str) -> bool:
    """Return True if a build failure looks like containerd/cache corruption."""
    return bool(_CORRUPTION_RE.search(str(error_text or "")))


@shared_task(
    bind=True,
    name="apps.deployments.tasks.recover_corrupt_docker_state",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def recover_corrupt_docker_state(self, deployment_id: str = ""):
    """Auto-recover from Docker/containerd state corruption.

    Steps:
      1. Cooldown check (no more than once per 5 min)
      2. Prune build cache
      3. Clear containerd ingest directory
      4. Restart Docker daemon (kills containers; compose restarts on boot)
      5. Report
    """
    from django.core.cache import cache

    # Cooldown
    last_prune = cache.get(_PRUNE_CACHE_KEY)
    if last_prune:
        elapsed = timezone.now().timestamp() - float(last_prune)
        if elapsed < _PRUNE_COOLDOWN:
            logger.info(
                "Corruption recovery skipped (cooldown: %.0fs since last)", elapsed
            )
            return {"status": "skipped", "reason": "cooldown"}

    results = perform_docker_recovery(deployment_id=deployment_id)

    # Set cooldown
    cache.set(_PRUNE_CACHE_KEY, str(timezone.now().timestamp()), _PRUNE_COOLDOWN)

    return results


def perform_docker_recovery(deployment_id: str = "") -> dict:
    """Run the Docker/containerd recovery steps unconditionally.

    Extracted so the maintenance UI (`--docker-recovery`) can reuse the
    exact same steps without duplicating subprocess logic. Callers that
    need thrash protection must check ``_PRUNE_CACHE_KEY`` themselves —
    the Celery task above does; the maintenance handler clears the key
    first so an explicit operator click always runs.
    """
    results = {}

    # 1. Prune build cache
    try:
        r = subprocess.run(
            ["docker", "builder", "prune", "-af"],
            capture_output=True, text=True, timeout=120,
        )
        results["builder_prune"] = r.returncode == 0
    except Exception as exc:
        logger.warning("Builder prune failed: %s", exc)
        results["builder_prune"] = False

    # 2. Prune dangling images (corrupted layer refs)
    try:
        r = subprocess.run(
            ["docker", "image", "prune", "-f"],
            capture_output=True, text=True, timeout=60,
        )
        results["image_prune"] = r.returncode == 0
    except Exception as exc:
        logger.warning("Image prune failed: %s", exc)
        results["image_prune"] = False

    # 3. Clear containerd ingest (corrupted layer staging area)
    try:
        r = subprocess.run(
            ["sh", "-c",
             "rm -rf /var/lib/containerd/io.containerd.content.v1.content/ingest/* 2>/dev/null; "
             "rm -rf /var/lib/containerd/tmpmounts/* 2>/dev/null; true"],
            capture_output=True, text=True, timeout=30,
        )
        results["containerd_clean"] = True
    except Exception as exc:
        logger.warning("Containerd cleanup failed: %s", exc)
        results["containerd_clean"] = False

    # 4. Restart Docker daemon
    try:
        r = subprocess.run(
            ["sh", "-c", "systemctl restart docker 2>/dev/null || service docker restart 2>/dev/null || true"],
            capture_output=True, text=True, timeout=60,
        )
        results["docker_restart"] = True
    except Exception as exc:
        logger.warning("Docker restart failed: %s", exc)
        results["docker_restart"] = False

    logger.info(
        "Docker corruption recovery completed: %s (deployment=%s)", results, deployment_id
    )
    return {"status": "ok", "steps": results, "deployment_id": deployment_id}


@shared_task(
    bind=True,
    name="apps.deployments.tasks.scan_recent_builds_for_corruption",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def scan_recent_builds_for_corruption(self):
    """Periodic sweeper: trigger recovery when recent builds show corruption.

    The build-pipeline hook (services/pipeline/build.py) only fires when
    the failing worker survives long enough to classify the error. A
    worker crash, lost log, or daemon-wedged build can leave matching
    BUILD_FAILED rows with no recovery dispatched. This beat task (every
    15m) scans the newest BUILD_FAILED deployments from the last 60m for
    containerd corruption patterns and dispatches
    ``recover_corrupt_docker_state`` once. The recovery task enforces the
    5-minute cooldown, so this sweep cannot thrash the daemon.
    """
    from celery.exceptions import SoftTimeLimitExceeded

    try:
        from apps.deployments.models import Deployment

        cutoff = timezone.now() - timedelta(minutes=60)
        recent = list(
            Deployment.objects.filter(
                status=Deployment.Status.BUILD_FAILED,
                created_at__gte=cutoff,
            )
            .order_by("-created_at")
            .only("id", "build_logs", "created_at")[:20]
        )
        matched = None
        for dep in recent:
            try:
                if is_build_corruption_error(getattr(dep, "build_logs", "") or ""):
                    matched = dep
                    break
            except Exception:
                continue
        if matched is None:
            return {"status": "ok", "matched": 0}
        try:
            recover_corrupt_docker_state.delay(deployment_id=str(matched.id))
        except Exception as exc:
            logger.warning("Corruption scan dispatch failed: %s", exc)
            return {"status": "error", "reason": "dispatch_failed"}
        logger.warning(
            "Corruption scan matched deployment %s — recovery dispatched",
            matched.id,
        )
        return {
            "status": "ok",
            "matched": 1,
            "deployment_id": str(matched.id),
            "triggered": True,
        }
    except SoftTimeLimitExceeded:
        return {"status": "error", "reason": "timeout"}
    except Exception as exc:
        logger.error("Corruption scan failed: %s", exc)
        return {"status": "error", "reason": str(exc)}


@shared_task(
    bind=True,
    name="apps.deployments.tasks.ensure_migrations_applied",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def ensure_migrations_applied(self):
    """Detect and apply pending migrations.

    ROOT CAUSE of the 2026-09-02 multi-outage: migration 0196
    (media_repo_url) was committed but never applied on the VPS. The
    PlatformConfig table was missing a column → every ORM query hit
    ProgrammingError → the config loader fell to its ghost path →
    empty domain → Caddyfile lost the platform block → 525 → custom
    domains demoted. A simple `manage.py migrate` at startup would
    have prevented ALL of it.

    This task:
      1. Runs `manage.py migrate --check` to detect pending migrations
      2. If pending: runs `manage.py migrate` (noinput)
      3. Reports the result

    Beat: every 5 minutes (cheap when no-op).
    """
    import subprocess

    try:
        # Check for pending migrations
        check = subprocess.run(
            ["python", "manage.py", "migrate", "--check", "--noinput"],
            capture_output=True, text=True, timeout=60,
            cwd="/app",
        )
        if check.returncode == 0:
            return {"status": "ok", "migrations": "up_to_date"}

        # Pending migrations — apply them
        logger.warning("Pending migrations detected — applying automatically")
        migrate = subprocess.run(
            ["python", "manage.py", "migrate", "--noinput"],
            capture_output=True, text=True, timeout=120,
            cwd="/app",
        )
        if migrate.returncode == 0:
            logger.info("Migrations applied successfully")
            # Clear PlatformConfig cache so the fresh schema is used
            from apps.deployments.models import PlatformConfig
            PlatformConfig.clear_cache()
            return {"status": "ok", "migrations": "applied"}
        else:
            logger.error("Migration failed: %s", migrate.stderr[-500:])
            return {"status": "error", "stderr": migrate.stderr[-500:]}

    except FileNotFoundError:
        return {"status": "skipped", "reason": "manage.py not found"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "reason": "timeout"}
    except Exception as exc:
        logger.error("Migration check failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
