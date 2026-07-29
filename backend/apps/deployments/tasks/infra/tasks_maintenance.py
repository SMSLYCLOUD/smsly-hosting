import logging

logger = logging.getLogger(__name__)
import subprocess

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM, TASK_TIME_LIMIT_STANDARD

from ..deploy.deletion import (  # noqa: F401
    _clear_orphaned_runtime_resources,
    _extract_addon_id_from_name,
    _is_stale_maintenance_container,
)
from ..remote.update import ThrottledLogAppender  # noqa: F401
from .tasks_platform_update import platform_update_task


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1], name="apps.deployments.tasks.run_maintenance_task")
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
            from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

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
            from .models.updates import PlatformUpdate

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

            update = PlatformUpdate.objects.create(
                initiated_by='system_maintenance',
                current_step='Initiating via maintenance task'
            )

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



@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.registry_garbage_collection_task")
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
