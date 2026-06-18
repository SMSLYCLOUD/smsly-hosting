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
import apps.deployments.tasks_safedeploy
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


def _regenerate_caddyfile():
    """Regenerate and apply the Caddyfile with current service domains.

    Called after successful deployments so new services get Caddy site blocks
    (and therefore SSL certificates) without requiring a manual Settings save.
    """
    try:
        config = PlatformConfig.load()
        from services.caddy_manager import generate_caddyfile, apply_caddyfile
        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            logger.info("Caddyfile regenerated after deployment")
        else:
            logger.warning("Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not regenerate Caddyfile: %s", exc)
