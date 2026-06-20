import logging

logger = logging.getLogger(__name__)
import logging  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402

from celery import shared_task  # noqa: E402


@shared_task(bind=True, max_retries=0)
def platform_update_task(self, update_id: str):
    """Execute platform update in background."""
    from services.platform_updater import perform_update

    from .models_updates import PlatformUpdate

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    perform_update(update)



@shared_task(bind=True, max_retries=0)
def platform_rollback_task(self, update_id: str):
    """Execute platform rollback in background (avoids blocking the request thread)."""
    from services.platform_updater import _rollback

    from .models_updates import PlatformUpdate

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    _rollback(update)



def _clear_directory_contents(path: str) -> dict:
    """Clear direct children of a known cache directory."""
    root = os.path.abspath(path)
    if root in {"/", "/app", "/opt", "/opt/smsly-hosting"}:
        raise ValueError(f"Refusing to clear unsafe directory: {root}")

    result = {"path": root, "removed": 0, "missing": False, "errors": []}
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
