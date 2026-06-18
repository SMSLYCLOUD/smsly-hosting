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

@shared_task(
    name='apps.deployments.tasks_autoscale.analyze_all_services_task',
    bind=True,
    ignore_result=True,
)
def analyze_all_services_task(self):
    """Periodic task: analyze active services and auto-scale as needed.

    Uses an ``id__gt`` cursor so the batch of 20 never silently drops
    services when more than 20 are candidates. Delegates each
    per-service decision to ``analyze_and_scale_service`` so the
    test suite (which patches that name) and the ``ScalingViewSet``
    REST endpoint share the same code path.
    """
    analyzed = 0
    last_id = None
    while True:
        base = ServiceReplica.objects.filter(status='RUNNING').values_list(
            'service_id', flat=True
        )
        qs = Service.objects.filter(status='RUNNING').distinct()
        qs = qs.filter(
            models.Q(id__in=base) | models.Q(compose_file='', deploy_mode='SINGLE')
        )
        if last_id is not None:
            qs = qs.filter(id__gt=last_id)
        batch = list(qs.order_by('id')[:AUTOSCALE_BATCH_SIZE])
        if not batch:
            break
        for svc in batch:
            try:
                analyze_and_scale_service(str(svc.id))
                analyzed += 1
            except Exception as exc:
                logger.warning("Auto-scale failed for %s: %s", svc.name, exc)
        last_id = batch[-1].id
    return {'analyzed': analyzed}


def analyze_and_scale_service(service_id):
    """Public entry point used by the Celery task, REST endpoint, and tests.

    Accepts a ``Service`` UUID string (from the Celery task / test mocks)
    or a ``Service`` instance (from the REST view). Delegates to the
    unified engine pipeline.
    """
    from apps.autoscaler.engine.pipeline import analyze_and_apply

    if isinstance(service_id, Service):
        service = service_id
    else:
        try:
            service = Service.objects.get(id=service_id)
        except (Service.DoesNotExist, ValueError, TypeError):
            logger.warning("Auto-scale task: service %s not found", service_id)
            return None
    return analyze_and_apply(service)

