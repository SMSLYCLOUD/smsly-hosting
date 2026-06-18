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


@shared_task(bind=True, max_retries=0)
def platform_update_task(self, update_id: str):
    """Execute platform update in background."""
    from .models_updates import PlatformUpdate
    from services.platform_updater import perform_update

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    perform_update(update)


@shared_task(bind=True, max_retries=0)
def platform_rollback_task(self, update_id: str):
    """Execute platform rollback in background (avoids blocking the request thread)."""
    from .models_updates import PlatformUpdate
    from services.platform_updater import _rollback

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
