from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from celery import shared_task
from apps.addons.services.addon_provisioner import addon_provisioner

from apps.deployments.constants import RETRY_DELAY_STANDARD, TASK_TIME_LIMIT_DATA_SYNC, TASK_TIME_LIMIT_MEDIUM
from apps.deployments.models import (
    EnvironmentVariable,
)
from apps.deployments.models.addons import Addon, Backup


@shared_task(bind=True, max_retries=3, soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.provision_addon_task")
def provision_addon_task(self, addon_id: str) -> None:
    """Provision an addon Docker container and inject env vars."""
    import time as _time
    _start_ts = _time.monotonic()
    try:
        addon = Addon.objects.get(id=addon_id)
        cid, url = addon_provisioner.provision_dispatch(addon)
        addon.connection_url = url
        addon.status = Addon.Status.ACTIVE
        addon.coolify_uuid = cid
        addon.save()
        try:
            from config.metrics import ADDON_PROVISION_DURATION
            ADDON_PROVISION_DURATION.labels(addon_type=addon.addon_type).observe(
                _time.monotonic() - _start_ts
            )
        except Exception as _metric_exc:
            logger.debug("addon provision metric failed: %s", _metric_exc)

        # If public domain is assigned, regenerate Caddy configuration
        if addon.public_domain:
            try:
                from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

                from apps.deployments.models.core import PlatformConfig
                cfg = PlatformConfig.load()
                caddy_content = generate_caddyfile(cfg)
                apply_caddyfile(caddy_content)
            except Exception as ce:
                logger.error("Failed to sync Caddy configuration for addon %s: %s", addon.id, ce)

        # Auto-inject addon credentials as env vars
        creds = addon.parsed_credentials
        for key, value in creds.items():
            EnvironmentVariable.objects.update_or_create(
                service=addon.service,
                key=key,
                defaults={
                    'value': value,
                    'is_secret': key.endswith('_PASSWORD') or key.endswith('_URL'),
                    'source': 'ADDON',
                }
            )

        # RabbitMQ: also inject common broker aliases for Celery/worker stacks
        if addon.addon_type == 'RABBITMQ':
            for extra_key in ("CELERY_BROKER_URL", "AMQP_URL"):
                EnvironmentVariable.objects.update_or_create(
                    service=addon.service,
                    key=extra_key,
                    defaults={'value': url, 'is_secret': True, 'source': 'ADDON'},
                )
    except Exception as e:
        # CalledProcessError str() omits stderr — include it so the real
        # daemon error (e.g. image pull failures) is visible in logs.
        _stderr = str(getattr(e, 'stderr', '') or '').strip()
        if _stderr:
            logger.error(
                "Addon provisioning failed for %s: %s\n--- stderr ---\n%s",
                addon_id, e, _stderr[-2000:],
            )
        else:
            logger.error("Addon provisioning failed for %s: %s", addon_id, e)
        try:
            addon = Addon.objects.get(id=addon_id)
            if self.request.retries >= self.max_retries:
                addon.status = Addon.Status.FAILED
                addon.save()
                logger.error("Addon %s marked FAILED after %d retries", addon_id, self.max_retries)
                return
        except Addon.DoesNotExist:
            return
        raise self.retry(exc=e, countdown=30)



@shared_task(bind=True, max_retries=3, soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.deprovision_addon_task")
def deprovision_addon_task(self, addon_id: str) -> None:
    """Delete addon container."""
    try:
        addon = Addon.objects.get(id=addon_id)
        if addon.coolify_uuid:
            container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
            addon_provisioner.deprovision_dispatch(addon.coolify_uuid, addon, container_name)
        addon.status = Addon.Status.DELETED
        addon.save()
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Deprovision failed: %s", e)
        raise self.retry(exc=e, countdown=30)



@shared_task(bind=True, max_retries=3, soft_time_limit=TASK_TIME_LIMIT_DATA_SYNC[0], time_limit=TASK_TIME_LIMIT_DATA_SYNC[1], name="apps.deployments.tasks.backup_addon_task")
def backup_addon_task(self, addon_id: str) -> None:
    """Create a backup for the specified addon."""
    backup = None
    try:
        addon = Addon.objects.get(id=addon_id)
        # Only create the Backup record on the first attempt.
        # Retries reuse the same record to avoid orphaned PENDING rows.
        if self.request.retries == 0:
            backup = Backup.objects.create(addon=addon, status=Backup.Status.PENDING)
        else:
            backup = Backup.objects.filter(
                addon=addon, status=Backup.Status.PENDING,
            ).order_by('-created_at').first()
            if not backup:
                backup = Backup.objects.create(addon=addon, status=Backup.Status.PENDING)
        path = addon_provisioner.create_backup(addon)
        backup.file_path = path
        backup.status = Backup.Status.COMPLETED
        backup.save()

        # Attempt to upload to cloud storage if a schedule exists
        try:
            import os

            from apps.cloud.models.backup import BackupSchedule
            from apps.cloud.services.backup_service import upload_backup_to_s3

            sched = BackupSchedule.objects.filter(
                service_id=addon.service_id, enabled=True, storage_backend='s3'
            ).first()
            if not sched:
                sched = BackupSchedule.objects.filter(
                    is_server_wide=True, enabled=True, storage_backend='s3'
                ).first()

            if sched and sched.s3_bucket and sched.s3_access_key:
                s3_key = f"smsly-backups/{addon.service.name}/addons/{os.path.basename(path)}"
                success = upload_backup_to_s3(
                    path, sched.s3_bucket, s3_key,
                    endpoint=sched.s3_endpoint, region=sched.s3_region,
                    access_key=sched.s3_access_key, secret_key=sched.s3_secret_key,
                )
                if success:
                    logger.info("Uploaded addon backup %s to %s/%s", addon_id, sched.s3_bucket, s3_key)
                else:
                    logger.error("Failed to upload addon backup %s: upload_backup_to_s3 returned False", addon_id)
        except Exception as up_exc:
            logger.error("Cloud upload skipped for addon %s: %s", addon_id, up_exc)
    except Exception as e:
        logger.error("Backup failed for addon %s: %s", addon_id, e)
        if self.request.retries >= self.max_retries:
            if backup:
                backup.status = Backup.Status.FAILED
                backup.error_message = str(e)[:500]
                backup.save()
            logger.error("Backup for addon %s marked FAILED after %d retries", addon_id, self.max_retries)
            return
        raise self.retry(exc=e, countdown=30)



@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_DATA_SYNC[0], time_limit=TASK_TIME_LIMIT_DATA_SYNC[1], max_retries=2, default_retry_delay=RETRY_DELAY_STANDARD, name="apps.deployments.tasks.restore_addon_task")
def restore_addon_task(self, backup_id: str) -> None:
    """Restore a backup to the addon."""
    try:
        backup = Backup.objects.get(id=backup_id)
        addon_provisioner.restore_backup(backup.addon, backup.file_path)
    except Backup.DoesNotExist:
        logger.warning("restore_addon_task: backup %s not found", backup_id)
    except Exception as exc:
        raise self.retry(exc=exc)



@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.delete_addon_task")
def delete_addon_task(self, addon_id: str) -> None:
    """Async reliable deletion of an Addon"""
    from apps.addons.services.addon_provisioner import addon_provisioner

    from apps.deployments.models.addons import Addon
    from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
    try:
        addon = Addon.objects.get(id=addon_id)
    except Addon.DoesNotExist:
        return

    # Remove HA components (standby/sentinels/proxy) before deleting the
    # primary so nothing orphans on the network.
    if getattr(addon, 'ha_enabled', False):
        try:
            from apps.addons.services.addon_ha import AddonHaManager
            from apps.addons.services.addon_provisioner import addon_provisioner
            manager = AddonHaManager(network_name=addon_provisioner.network_name)
            manager.teardown(addon)
        except Exception:
            logger.warning(
                "HA teardown failed for addon %s; continuing with deletion",
                addon_id, exc_info=True,
            )

    # Remote full-stack node addons: deprovision via SSH
    server = getattr(addon.service, 'server', None)
    if (server and not server.is_primary
            and not getattr(server, 'is_lite_agent', False)):
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        success = addon_provisioner.deprovision_remote(
            addon.coolify_uuid or container_name, server, container_name,
        )
    else:
        orchestrator = DeletionOrchestrator()
        success = orchestrator.delete_addon_resources(addon)
        # Resilience: If local docker client is missing
        if not success and not orchestrator.docker_client:
            logger.warning("Docker client unavailable for addon %s. Forcing database-only deletion.", addon.id)
            success = True

    if success:
        addon.delete()
    else:
        addon.status = Addon.Status.DELETION_FAILED
        addon.deletion_error = "Failed to remove some runtime resources. If the system is offline, use manual DB cleanup."
        addon.save(update_fields=['status', 'deletion_error'])
