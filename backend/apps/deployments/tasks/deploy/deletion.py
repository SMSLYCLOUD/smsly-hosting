"""Deletion tasks — service deletion, orphaned resource cleanup, stalled recovery."""
import logging
import os
import shutil

import docker
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone
from apps.addons.services.addon_provisioner import addon_provisioner

from apps.deployments.constants import (
    STALL_RECOVERY_BATCH_SIZE,
    STALL_RECOVERY_THRESHOLD_MINUTES,
    TASK_TIME_LIMIT_QUICK,
    TASK_TIME_LIMIT_STANDARD,
)
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator

logger = logging.getLogger(__name__)
@shared_task(bind=True, name="apps.deployments.tasks.recover_stalled_deletions", soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1])
def recover_stalled_deletions(self):
    """Periodic task: re-queue services stuck in DELETION_PENDING for too long."""
    from datetime import timedelta


    threshold = timezone.now() - timedelta(minutes=STALL_RECOVERY_THRESHOLD_MINUTES)
    services = list(
        Service.objects.filter(
            status=Service.Status.DELETION_PENDING,
            updated_at__lt=threshold,
        ).values_list("id", flat=True)[:STALL_RECOVERY_BATCH_SIZE]
    )
    if not services:
        return {"recovered": 0}
    for sid in services:
        try:
            delete_service_task.delay(str(sid))
        except Exception as exc:
            logger.warning("Failed to requeue delete for service=%s: %s", sid, exc)
    logger.info("Requeued %d stalled deletion(s)", len(services))
    return {"recovered": len(services)}

def _clear_directory_contents(path: str) -> dict:
    """Clear direct children of a known cache directory."""
    root = os.path.abspath(path)
    # Only allow clearing under known safe roots
    allowed_roots = {"/opt/smsly-cache"}
    if not any(root == r or root.startswith(r + "/") for r in allowed_roots):
        raise ValueError(f"Refusing to clear directory outside allowed roots: {root}")

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
    if status_value not in {"exited", "created", "dead"}:
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
        filters={"status": ["exited", "created", "dead"]},
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

@shared_task(bind=True, max_retries=3, soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1], name="apps.deployments.tasks.delete_service_task")
def delete_service_task(self, service_id: str, force: bool = False):
    """Async reliable deletion of a Service"""
    from apps.deployments.models.core import Service
    from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        return

    success = False

    try:
        # 1. Handle remote server cleanup if applicable
        try:
            from apps.deployments.utils.target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            active_server = target["server_obj"]
        except Exception:
            active_server = getattr(service, 'server', None)

        if active_server and not active_server.is_primary:
            try:
                logger.info("Decommissioning service %s on remote node %s", service.name, active_server.host)
                remote = RemoteOrchestrator(active_server)
                success = remote.delete_service_for_local(service, force=force)

                # If force=True, we proceed even if remote call fails (best-effort local cleanup)
                if force:
                    success = True
            except Exception as exc:
                logger.warning("Remote deletion failed for service %s: %s.", service.name, exc)
                success = force
        else:
            # 2. Local cleanup
            orchestrator = DeletionOrchestrator()
            success = orchestrator.delete_service_resources(service, force=force)

            # 2b. Clean up addon runtime resources before DB cascade
            for addon in service.addons.all():
                server = getattr(addon.service, 'server', None)
                if (server and not server.is_primary
                        and not getattr(server, 'is_lite_agent', False)):
                    container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
                    ok = addon_provisioner.deprovision_remote(
                        addon.coolify_uuid or container_name, server, container_name,
                    )
                elif orchestrator.docker_client:
                    ok = orchestrator.delete_addon_resources(addon)
                else:
                    ok = True
                if not ok:
                    logger.warning("Failed to clean up addon %s (%s) for service %s.",
                                   addon.id, addon.addon_type, service.name)
                    if not force:
                        success = False

            # 3. Resilience: If force=True, we proceed regardless of resource cleanup success.
            # This ensures the DB record is purged when the user explicitly requests a force-delete.
            if force:
                logger.info("Force-purging service %s from database after best-effort cleanup.", service.name)
                success = True
            elif not success and not service.server and not orchestrator.docker_client:
                logger.warning("Docker client unavailable for service %s. Forcing database-only deletion.", service.name)
                success = True

        if success:
            # Capture project reference and owner before deleting the service.
            service_project = getattr(service, 'project', None)
            service_owner_id = service.owner_id

            # GDPR right-to-erasure: delete all backup tarballs and DB rows
            # owned by this service's user BEFORE the CASCADE fires. The
            # backup file paths are not recoverable once the ServiceBackup row
            # is gone.
            try:
                from .services.backup_service import purge_user_backups
                purge_user_backups(service_owner_id)
            except Exception as cleanup_exc:
                logger.warning(
                    "Backup purge during service deletion failed for %s: %s",
                    service.id, cleanup_exc,
                )

            try:
                service.delete()
            except Exception as del_exc:
                from django.db import ProgrammingError as _PE
                if isinstance(del_exc, _PE) and 'does not exist' in str(del_exc):
                    logger.warning(
                        "Optional table missing during delete of %s — "
                        "force-deleting via raw SQL: %s",
                        service.id, del_exc,
                    )
                    from django.db import connection
                    with connection.cursor() as cur:
                        cur.execute(
                            "DELETE FROM deployments_addon WHERE service_id = %s",
                            [service.id],
                        )
                        cur.execute(
                            "DELETE FROM deployments_deployment WHERE service_id = %s",
                            [service.id],
                        )
                        cur.execute(
                            "DELETE FROM deployments_service WHERE id = %s",
                            [service.id],
                        )
                else:
                    raise

            # After deleting an LLM consumer, check if shared Ollama CPP
            # is still needed. If no remaining services need it, clean it up
            # to free VPS resources.
            if service_project:
                try:
                    from ..ollama import _cleanup_shared_ollama_if_unused
                    _cleanup_shared_ollama_if_unused(service_project)
                except Exception as cleanup_exc:
                    logger.warning("Shared Ollama cleanup check failed for project %s: %s",
                                   service_project.id, cleanup_exc)
        else:
            service.status = Service.Status.DELETION_FAILED
            service.deletion_error = "Failed to remove some runtime resources. If this node is unassigned or unreachable, use 'Retry' or manual DB cleanup."
            service.save(update_fields=['status', 'deletion_error'])

    except SoftTimeLimitExceeded:
        logger.error("Soft time limit exceeded deleting service %s", service_id)
        service.refresh_from_db()
        if service.status != Service.Status.DELETION_FAILED:
            service.status = Service.Status.DELETION_FAILED
            service.deletion_error = "Deletion timed out. Please retry or use manual cleanup."
            service.save(update_fields=['status', 'deletion_error'])
    except self.MaxRetriesExceededError:
        logger.error("Max retries exceeded for delete_service_task service=%s", service_id)
        service.refresh_from_db()
        if service.status != Service.Status.DELETION_FAILED:
            service.status = Service.Status.DELETION_FAILED
            service.deletion_error = "Deletion failed after multiple retries. Please use manual cleanup."
            service.save(update_fields=['status', 'deletion_error'])
    except Exception as exc:
        logger.exception("delete_service_task failed for service=%s: %s", service_id, exc)
        raise self.retry(exc=exc, countdown=30)
