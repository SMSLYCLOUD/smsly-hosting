import logging
logger = logging.getLogger(__name__)
import logging
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



@shared_task(bind=True, max_retries=3)
def provision_addon_task(self, addon_id: str):
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
                from .models import PlatformConfig
                from services.caddy_manager import generate_caddyfile, apply_caddyfile
                cfg = PlatformConfig.load()
                caddy_content = generate_caddyfile(cfg)
                apply_caddyfile(caddy_content)
            except Exception as ce:
                logger.warning("Failed to sync Caddy configuration for addon %s: %s", addon.id, ce)

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



@shared_task
def deprovision_addon_task(addon_id: str):
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



@shared_task(bind=True, max_retries=3)
def backup_addon_task(self, addon_id: str):
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



@shared_task(bind=True)
def restore_addon_task(self, backup_id: str):
    """Restore a backup to the addon."""
    # pylint: disable=unused-argument
    try:
        backup = Backup.objects.get(id=backup_id)
        addon_provisioner.restore_backup(backup.addon, backup.file_path)
    except Exception as e:
        raise e



@shared_task(bind=True, max_retries=3)
def delete_addon_task(self, addon_id: str):
    """Async reliable deletion of an Addon"""
    from apps.deployments.models_addons import Addon
    from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
    from services.addon_provisioner import addon_provisioner
    try:
        addon = Addon.objects.get(id=addon_id)
    except Addon.DoesNotExist:
        return

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
