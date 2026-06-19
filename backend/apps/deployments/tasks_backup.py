import logging
logger = logging.getLogger(__name__)
import random
import re
import shlex
import shutil
import tempfile
import subprocess
import os
import json
import time
import zipfile
import secrets
import threading
from contextlib import contextmanager
from urllib.parse import unquote, urlparse
import docker
import requests
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.db.models import Sum
from apps.cloud.models import CloudProvider
from apps.cloud.services.builder import NixpacksBuilder
from apps.cloud.services.compute import ComputeService
from apps.cloud.services.function_provisioner import FunctionProvisioner
from apps.deployments.ai_router import DEFAULT_AI_ROUTER_API_BASE, DEFAULT_AI_ROUTER_UI_BASE, DEFAULT_BRAID_ALIAS, generate_ai_router_proxy_config, get_ollama_model_name, is_ai_router_service, is_ollama_service
from apps.deployments.models import Service, Deployment, EnvironmentVariable, PlatformConfig
from apps.deployments.models_addons import Addon, Backup
from apps.deployments.models_backup import BackupSchedule, ServiceBackup
from apps.deployments.models_storage import Volume
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.backup_service import BackupService
from apps.deployments.services.pipeline import PipelineManager, PipelineError
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.services.tls_verify import should_verify
from apps.deployments.services.transfer_service import ServerTransferService
from apps.deployments.utils import append_log, broadcast_status, build_local_source_bundle, update_stage, is_deployment_local
from services.addon_provisioner import addon_provisioner



@shared_task(bind=True, soft_time_limit=3600, time_limit=3900, max_retries=3, default_retry_delay=300)
def create_service_backup_task(self, service_id, backup_type='MANUAL', backup_id=None, schedule_id=None):
    from .services.backup_service import BackupService
    from apps.deployments.utils import log_event
    log_event(
        action='BACKUP_CREATE',
        target=f'Service: {service_id}',
        actor='system',
        metadata={
            'service_id': str(service_id),
            'backup_id': str(backup_id) if backup_id else None,
            'backup_type': backup_type,
        },
    )
    try:
        backup_service = BackupService()
        backup_service.backup_service(service_id, backup_id=backup_id, backup_type=backup_type)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise
    finally:
        # Update the schedule's last_run only AFTER the backup completes
        # (success or failure). This prevents the race where last_run was
        # set before the task ran, causing missed schedules on failure.
        if schedule_id:
            _touch_schedule_last_run(schedule_id)



def _touch_schedule_last_run(schedule_id):
    """Update the BackupSchedule.last_run to now (called after backup completes)."""
    try:
        from django.utils import timezone as tz
        BackupSchedule.objects.filter(id=schedule_id).update(last_run=tz.now())
    except Exception:
        pass


@shared_task(bind=True, soft_time_limit=7200, time_limit=7500, max_retries=2, default_retry_delay=600)
def create_server_backup_task(self, backup_id=None, schedule_id=None):
    log_event(
        action='BACKUP_CREATE',
        target='Server',
        actor='system',
        metadata={
            'backup_id': str(backup_id) if backup_id else None,
            'scope': 'server',
        },
    )
    backup_service = BackupService()
    backup_service.backup_server(backup_id=backup_id)
    if schedule_id:
        _touch_schedule_last_run(schedule_id)



@shared_task(bind=True, soft_time_limit=3600, max_retries=2, default_retry_delay=300)
def restore_service_backup_task(self, backup_id, target_service_id=None, requesting_user_id=None, raise_on_snapshot_failure=False):
    log_event(
        action='BACKUP_RESTORE',
        target=f'Backup: {backup_id}',
        actor='system',
        metadata={
            'backup_id': str(backup_id),
            'target_service_id': str(target_service_id) if target_service_id else None,
            'requesting_user_id': str(requesting_user_id) if requesting_user_id else None,
            'scope': 'service',
        },
    )
    backup_service = BackupService()
    backup_service.restore_service(
        backup_id,
        target_service_id=target_service_id,
        requesting_user_id=requesting_user_id,
        raise_on_snapshot_failure=raise_on_snapshot_failure,
    )



@shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
def restore_server_backup_task(self, backup_id, requesting_user_id=None):
    log_event(
        action='BACKUP_RESTORE',
        target=f'Backup: {backup_id}',
        actor='system',
        metadata={
            'backup_id': str(backup_id),
            'requesting_user_id': str(requesting_user_id) if requesting_user_id else None,
            'scope': 'server',
        },
    )
    backup_service = BackupService()
    backup_service.restore_server(backup_id=backup_id, requesting_user_id=requesting_user_id)



@shared_task(bind=True, soft_time_limit=7200, time_limit=7500, max_retries=2, default_retry_delay=120)
def purge_user_backups_task(self, user_id, actor: str = 'system', force: bool = False):
    """
    GDPR right-to-erasure background task.

    Should be enqueued BEFORE the user account is deleted so that the
    ``ServiceBackup.service`` FK still resolves. The task:
      1. Removes ``ServiceBackup.file_path`` tarballs for every service
         owned by the user.
      2. Removes any matching cloud-storage object (S3 / R2 / MinIO).
      3. Deletes the ``ServiceBackup`` and ``ServerBackup`` rows.
      4. Emits an ``AuditLog`` entry recording the count of artifacts purged.
    """
    from apps.deployments.services.backup_service import purge_user_backups
    from apps.deployments.models_audit import AuditLog

    try:
        counters = purge_user_backups(user_id)
    except Exception as exc:
        logger.error("purge_user_backups_task failed for user %s: %s", user_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        if not force:
            raise

    try:
        AuditLog.objects.create(
            actor=actor or 'system',
            action='USER_BACKUPS_PURGED',
            target=f'User: {user_id}',
            metadata={
                'user_id': str(user_id),
                'service_backups_deleted': counters.get('service_backups_deleted', 0),
                'service_backup_files_deleted': counters.get('service_backup_files_deleted', 0),
                'server_backups_deleted': counters.get('server_backups_deleted', 0),
                'server_backup_files_deleted': counters.get('server_backup_files_deleted', 0),
                'cloud_objects_deleted': counters.get('cloud_objects_deleted', 0),
                'errors': counters.get('errors', 0),
            },
        )
    except Exception as exc:
        logger.warning("Failed to record USER_BACKUPS_PURGED audit log: %s", exc)

    return counters



@shared_task
def cleanup_old_backups_task():
    """Delete backups older than retention_days per schedule."""
    from .models_backup import BackupSchedule, ServiceBackup, ServerBackup
    from datetime import timedelta

    schedules = BackupSchedule.objects.filter(enabled=True)
    cleaned = 0
    for sched in schedules:
        try:
            cutoff = timezone.now() - timedelta(days=sched.retention_days)
            if sched.service:
                old = ServiceBackup.objects.filter(
                    service=sched.service, created_at__lt=cutoff
                ).exclude(backup_type='TRANSFER')
                for b in old:
                    if b.file_path and os.path.exists(b.file_path):
                        os.remove(b.file_path)
                    b.delete()
                    cleaned += 1
            elif sched.is_server_wide:
                old = ServerBackup.objects.filter(created_at__lt=cutoff)
                for b in old:
                    if b.file_path and os.path.exists(b.file_path):
                        os.remove(b.file_path)
                    b.delete()
                    cleaned += 1
        except Exception as exc:
            logger.warning("Backup cleanup failed for schedule %s: %s", sched.id, exc)
    return cleaned



@shared_task
def run_scheduled_backups_task():
    """Execute all due BackupSchedule entries."""
    from .models_backup import BackupSchedule
    import croniter
    from datetime import datetime

    now = timezone.now()
    schedules = BackupSchedule.objects.filter(enabled=True)
    ran = 0
    for sched in schedules:
        try:
            cron = croniter.croniter(sched.cron_expression, now)
            next_run = cron.get_next(datetime)
            if sched.last_run and sched.last_run >= timezone.make_aware(datetime.fromtimestamp(next_run), timezone.get_current_timezone()):
                continue
            # Compute next_run now but defer last_run — the Celery task
            # updates it AFTER the backup completes to prevent the race
            # where a failed task leaves the schedule thinking it ran.
            sched.next_run = timezone.make_aware(datetime.fromtimestamp(cron.get_next(datetime)))
            sched.save(update_fields=['next_run'])

            if sched.is_server_wide:
                create_server_backup_task.delay(schedule_id=sched.id)
            elif sched.service:
                create_service_backup_task.delay(str(sched.service.id), backup_type='SCHEDULED', schedule_id=sched.id)
            ran += 1
        except Exception as exc:
            logger.warning("Scheduled backup failed for schedule %s: %s", sched.id, exc)
    return ran
