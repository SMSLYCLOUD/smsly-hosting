import logging

logger = logging.getLogger(__name__)
import logging
import os
import shutil

from celery import shared_task

from apps.deployments.constants import (
    RETRY_DELAY_FAST,
    TASK_TIME_LIMIT_HEAVY,
)


@shared_task(bind=True, max_retries=0, soft_time_limit=TASK_TIME_LIMIT_HEAVY[0], time_limit=TASK_TIME_LIMIT_HEAVY[1], acks_late=False, reject_on_worker_lost=False, name="apps.deployments.tasks.platform_update_task")
def platform_update_task(self, update_id: str):
    """Execute platform update in background."""
    from apps.deployments.services.platform_updater import perform_update

    from .models.updates import PlatformUpdate

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    if update.status != 'PENDING':
        logger.warning(
            "Platform update %s is already in state %s; skipping re-execution to prevent restart loop.",
            update_id, update.status,
        )
        return

    perform_update(update)



@shared_task(bind=True, max_retries=2, default_retry_delay=RETRY_DELAY_FAST, soft_time_limit=TASK_TIME_LIMIT_HEAVY[0], time_limit=TASK_TIME_LIMIT_HEAVY[1], acks_late=False, reject_on_worker_lost=False, name="apps.deployments.tasks.platform_rollback_task")
def platform_rollback_task(self, update_id: str):
    """Execute platform rollback in background (avoids blocking the request thread).

    Retries on transient failures (network blips during git fetch / install.sh)
    up to max_retries with a 30s backoff. Uses a cache lock to deduplicate
    concurrent rollback requests for the same update.
    """
    from django.core.cache import cache
    from apps.deployments.services.platform_updater import _rollback

    from .models.updates import PlatformUpdate

    lock_key = f"platform-rollback:{update_id}"
    if not cache.add(lock_key, "1", timeout=1800):
        logger.warning(
            "Platform rollback task: duplicate rollback ignored for %s", update_id,
        )
        return {"status": "skipped", "reason": "already_running"}

    try:
        try:
            update = PlatformUpdate.objects.get(id=update_id)
        except PlatformUpdate.DoesNotExist:
            return {"status": "missing"}

        if update.status in {'ROLLED_BACK', 'FAILED'}:
            logger.warning(
                "Platform rollback %s is already in terminal state %s; skipping re-execution.",
                update_id, update.status,
            )
            return {"status": "skipped", "reason": f"already_{update.status.lower()}"}

        _rollback(update)
        return {"status": update.status}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Retry on transient errors (network, git fetch blip) — _rollback
        # already records FAILED state for permanent errors.
        logger.exception(
            "Platform rollback task raised for %s (attempt %s): %s",
            update_id, self.request.retries + 1, exc,
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            cache.delete(lock_key)
            return {"status": "failed", "error": str(exc)}
    finally:
        # Release the lock so a future manual rollback can proceed.
        cache.delete(lock_key)



def _clear_directory_contents(path: str) -> dict:
    """Clear direct children of a known cache directory."""
    root = os.path.abspath(path)
    if root in {"/", "/app", "/opt", "/opt/smsly-hosting"}:
        raise ValueError(f"Refusing to clear unsafe directory: {root}")

    result: dict = {"path": root, "removed": 0, "missing": False, "errors": []}
    if not os.path.isdir(root):
        result["missing"] = True
        return result

    for item in os.listdir(root):
        item_path = os.path.abspath(os.path.join(root, item))
        if os.path.commonpath([root, item_path]) != root:
            result["errors"].append(f"Skipped unsafe path: {item_path}")
            continue
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
            result["removed"] += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to clear cache item %s: %s", item_path, exc)
            result["errors"].append(f"{item_path}: {exc}")
    return result
