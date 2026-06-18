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


AUTO_APPROVE_COMMIT_MARKERS = (
    "auto-redeploy",
    "auto-remediation",
    "auto-rollback",
    "auto-restart",
    "[auto-fix]",
    "service restart",
)
def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, str(default))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def should_skip_review_for_commit_message(message: str) -> bool:
    """Return True for system-created deployments that must not pause at REVIEW."""
    normalized = str(message or "").strip().lower()
    return any(marker in normalized for marker in AUTO_APPROVE_COMMIT_MARKERS)


def _current_agent_node_queue() -> str:
    """Return this lite agent's dedicated deploy queue, if running as an agent."""
    if str(os.environ.get("MODE", "")).strip().lower() != "agent":
        return ""
    queue = str(os.environ.get("SMSLY_NODE_QUEUE", "")).strip()
    if not queue or queue == "deploy":
        logger.warning(
            "Agent mode is running without a dedicated SMSLY_NODE_QUEUE; "
            "falling back to the shared deploy queue."
        )
        return ""
    return queue
