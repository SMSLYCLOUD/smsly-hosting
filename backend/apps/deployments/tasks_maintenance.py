import logging

logger = logging.getLogger(__name__)
import contextlib
import logging
import subprocess
import time

import docker
from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.models import (
    Service,
)
from apps.deployments.models_addons import Addon

from .tasks_platform_update import _clear_directory_contents, platform_update_task


def _extract_addon_id_from_name(name: str) -> str:
    prefix = "smsly-addon-"
    if not name.startswith(prefix):
        return ""
    remainder = name[len(prefix):]
    parts = remainder.split("-", 1)
    return parts[1] if len(parts) == 2 else ""



def _is_stale_maintenance_container(
    container,
    *,
    active_service_ids: set,
    active_addon_ids: set,
    active_service_names: set,
) -> tuple[bool, str]:
    name = str(getattr(container, "name", "") or "")
    labels = getattr(container, "labels", None) or {}
    status_value = str(getattr(container, "status", "") or "").lower()
    if status_value not in {"exited", "created", "dead", "restarting"}:
        return False, "container is not stopped"

    service_id = str(labels.get("smsly.service_id") or "").strip()
    addon_id = str(labels.get("smsly.addon_id") or "").strip()
    canonical_name = str(labels.get("smsly.blue_green.canonical_name") or "").strip()

    if "-green-" in name:
        return True, "stale blue-green candidate"

    if addon_id:
        return addon_id not in active_addon_ids, "addon missing from DB"

    if service_id:
        return service_id not in active_service_ids, "service missing from DB"

    inferred_addon_id = _extract_addon_id_from_name(name)
    if inferred_addon_id:
        return inferred_addon_id not in active_addon_ids, "addon name missing from DB"

    if name.startswith("ai-router"):
        if canonical_name and canonical_name in active_service_names:
            return False, "active AI router service"
        return name not in active_service_names, "stale AI router"

    if labels.get("managed_by") == "smsly-hosting" and canonical_name:
        return canonical_name not in active_service_names, "managed service missing from DB"

    return False, "not a managed stale container"



def _clear_orphaned_runtime_resources() -> dict:
    client = docker.from_env()
    active_service_ids = {
        str(value)
        for value in Service.objects.exclude(status__in=["DELETED", "DELETION_PENDING"]).values_list("id", flat=True)
    }
    active_service_names = {
        str(value)
        for value in Service.objects.exclude(status__in=["DELETED", "DELETION_PENDING"]).values_list("name", flat=True)
    }
    active_addon_ids = {
        str(value)
        for value in Addon.objects.exclude(status="DELETED").values_list("id", flat=True)
    }

    removed = []
    skipped = []
    errors = []
    containers = client.containers.list(
        all=True,
        filters={"status": ["exited", "created", "dead", "restarting"]},
    )
    for container in containers:
        should_remove, reason = _is_stale_maintenance_container(
            container,
            active_service_ids=active_service_ids,
            active_addon_ids=active_addon_ids,
            active_service_names=active_service_names,
        )
        if not should_remove:
            skipped.append({"name": container.name, "reason": reason})
            continue

        try:
            container.remove(force=True)
            removed.append({"name": container.name, "reason": reason})
            logger.info("Removed orphaned container %s: %s", container.name, reason)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to remove orphaned container %s: %s", container.name, exc)
            errors.append({"name": container.name, "error": str(exc)})

    image_prune: dict = {}
    try:
        image_prune = client.images.prune(filters={"dangling": ["false"]}) or {}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to prune unused images: %s", exc)
        errors.append({"name": "unused-images", "error": str(exc)})

    cache_results = [
        _clear_directory_contents("/opt/smsly-cache"),
    ]

    return {
        "removed": removed,
        "removed_count": len(removed),
        "skipped_count": len(skipped),
        "errors": errors,
        "cache": cache_results,
        "images_reclaimed_bytes": image_prune.get("SpaceReclaimed", 0),
    }



@shared_task(bind=True, soft_time_limit=300, time_limit=360, name="apps.deployments.tasks.run_maintenance_task")
def run_maintenance_task(self, command_flag: str, lock_key: str = ""):
    """
    Run maintenance commands via the Docker API from inside the Celery container.
    Valid flags: --clear, --update, --refresh
    """
    if command_flag not in ['--clear', '--update', '--update-frontend', '--refresh']:
        logger.error(f"Invalid maintenance command: {command_flag}")
        return {"status": "error", "reason": "invalid_command", "message": "Invalid maintenance command."}

    try:
        logger.info(f"Running maintenance command: {command_flag}")
        self.update_state(
            state="STARTED",
            meta={
                "status": "running",
                "message": f"Running maintenance command {command_flag}.",
            },
        )

        if command_flag == '--clear':
            details = _clear_orphaned_runtime_resources()
            return {
                "status": "success",
                "message": (
                    "Cleanup complete. Removed "
                    f"{details['removed_count']} orphaned container(s) and flushed cache directories."
                ),
                "details": details,
            }

        elif command_flag == '--refresh':
            # Restart caddy via the shared volume .reload flag
            from services.caddy_manager import apply_caddyfile, generate_caddyfile

            from apps.deployments.models import PlatformConfig

            config = PlatformConfig.load()
            content = generate_caddyfile(config)
            cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()

            result = apply_caddyfile(content, cloudflare_token=cf_token)
            if result.get('ok'):
                logger.info("Proxy refresh flag written to shared volume successfully.")
                return {
                    "status": "success",
                    "message": "Proxy refresh flag written. The host will reload Caddy shortly.",
                    "details": result,
                }
            else:
                return {
                    "status": "error",
                    "message": result.get('message', 'Failed to write proxy reload flag.'),
                    "details": result,
                }

        elif command_flag in ['--update', '--update-frontend']:
            from .models_updates import PlatformUpdate

            # Clear any stuck/stale in-progress updates before starting a new one
            stale_in_progress = PlatformUpdate.objects.filter(
                status__in=['PENDING', 'PULLING', 'BACKING_UP', 'RESTARTING', 'HEALTH_CHECK', 'MIGRATING']
            )
            if stale_in_progress.exists():
                cleared_count = 0
                for stale in stale_in_progress:
                    stale.status = 'FAILED'
                    stale.error_message = 'Cleared stale update to allow new update to proceed.'
                    stale.completed_at = timezone.now()
                    stale.append_log('✗ Cleared as stale to allow new update to proceed.')
                    stale.save()
                    cleared_count += 1
                    logger.info("Cleared stale platform update %s (was %s)", stale.id, stale.status)

                if cleared_count:
                    self.update_state(
                        state="STARTED",
                        meta={
                            "status": "running",
                            "message": f"Cleared {cleared_count} stale update(s). Starting fresh update...",
                        },
                    )

            # Create the update record
            update = PlatformUpdate.objects.create(
                initiated_by='system_maintenance',
                current_step='Initiating via maintenance task'
            )

            # Trigger the resilient update task
            platform_update_task.delay(update_id=str(update.id))

            logger.info(f"Platform update {update.id} initiated via maintenance action.")
            return {
                "status": "success",
                "message": "Platform update initiated using the resilient updater. You can track progress in the Platform Updates log.",
                "task_id": str(update.id)
            }

    except Exception as e:
        logger.exception(f"Exception during maintenance {command_flag}: {e}")
        return {"status": "error", "reason": str(e), "message": f"Maintenance failed: {e}"}
    finally:
        if lock_key:
            cache.delete(lock_key)



class ThrottledLogAppender:
    """Buffers and throttles database saves for remote server update logs to avoid lockups."""
    def __init__(self, server, interval=1.5):
        self.server = server
        self.interval = interval
        self.buffer = ""
        self.last_save = time.time()

    def append(self, text):
        if not text:
            return
        self.buffer += text
        now = time.time()
        if now - self.last_save >= self.interval:
            self.flush()

    def flush(self):
        from .tasks_server_update import _append_remote_update_log

        if self.buffer:
            with contextlib.suppress(Exception):
                self.server.refresh_from_db(fields=["provision_logs"])
            _append_remote_update_log(self.server, self.buffer)
            self.buffer = ""
            self.last_save = time.time()



@shared_task(soft_time_limit=600, time_limit=900, name="apps.deployments.tasks.registry_garbage_collection_task")
def registry_garbage_collection_task():
    """
    Periodically run Docker registry garbage collection to reclaim disk
    space from deleted/unused image layers.

    Runs: docker exec <registry> registry garbage-collect /etc/docker/registry/config.yml
    Removes blobs that are no longer referenced by any manifest.
    Safe to run while the registry is serving reads.
    """
    registry_container = "smsly-hosting-registry-1"

    try:
        dry_run = subprocess.run(
            ["docker", "exec", registry_container, "registry", "garbage-collect",
             "--dry-run", "/etc/docker/registry/config.yml"],
            capture_output=True, text=True, timeout=120,
        )
        if dry_run.returncode != 0:
            logger.warning("registry_gc: dry-run failed: %s", dry_run.stderr[:500])
            return

        freed_lines = [line for line in dry_run.stdout.split('\n') if 'marking blob' in line.lower() or 'blob eligible' in line.lower()]
        logger.info("registry_gc: dry-run found %d blobs eligible for removal", len(freed_lines))

        result = subprocess.run(
            ["docker", "exec", registry_container, "registry", "garbage-collect",
             "/etc/docker/registry/config.yml"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'recovered' in line.lower() or 'blob' in line.lower():
                    logger.info("registry_gc: %s", line.strip())
            logger.info("registry_gc: garbage collection completed successfully")
        else:
            logger.warning("registry_gc: failed: %s", result.stderr[:500])

    except subprocess.TimeoutExpired:
        logger.error("registry_gc: timed out")
    except Exception as e:
        logger.error("registry_gc: error: %s", e)
