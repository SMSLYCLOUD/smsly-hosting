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

@shared_task
def check_cron_jobs():
    """
    Periodic task to check for due cron jobs.
    This should be run every minute by Celery Beat.
    """
    now = timezone.now()
    jobs = CronJob.objects.filter(is_active=True)

    # In a real implementation, we would use a cron library to check
    # if the 'schedule' matches 'now'. For now, we simulate execution
    # if it hasn't run in the last X minutes.

    for job in jobs:
        # Simplification: Assume all jobs run every minute for demo
        # Real logic: if croniter(job.schedule).is_due(now): ...

        trigger_cron_job.delay(job_id=str(job.id))


@shared_task
def trigger_cron_job(job_id):
    try:
        job = CronJob.objects.get(id=job_id)
        service = job.service

        # Find active deployment container
        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy or not latest_deploy.container_id:
            logger.warning(f"No active container for cron job {job.name}")
            return

        adapter = LocalAdapter()
        # Use exec_container to run the command
        # Note: This is simplified. exec_container returns a socket for interactive use.
        # We need a non-interactive exec.
        # We'll assume the adapter has a method or we'll add one.

        # Let's use docker client directly for one-off exec if needed,
        # or enhance adapter.
        if adapter.docker_client:
            container = adapter.docker_client.containers.get(
                latest_deploy.container_id)
            exit_code, output = container.exec_run(job.command, detach=False)

            logger.info(
                f"Cron {job.name} finished with exit code {exit_code}. Output: {output.decode('utf-8')[:100]}...")

            job.last_run_at = timezone.now()
            job.save()

    except Exception as e:
        logger.error(f"Failed to run cron job {job_id}: {e}")
