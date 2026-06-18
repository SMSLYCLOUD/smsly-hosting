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

def _sanitize_for_llm(logs: str) -> str:
    """Neutralize common prompt-injection patterns in untrusted log text.

    The build/runtime logs come from arbitrary source code and may
    contain strings crafted to hijack the LLM. This function replaces
    known-injection patterns with a benign redaction token so the LLM
    sees the line existed without acting on the instructions.

    The output is intentionally conservative: real error messages may
    contain words like "ignore" or "act as" (e.g. "ignore the previous
    version") — replacing the *pattern* is safer than rejecting the
    line. The replacement token is the same in every case so the model
    can recognise it as a marker.
    """
    out = _HIDDEN_UNICODE_CHARS.sub("", logs)
    for pat in _INJECTION_PATTERNS:
        out = pat.sub("[redacted-injection]", out)
    return out


@shared_task
def analyze_failure_task(deployment_id):
    """
    Uses Jules AI (via SMSLY Platform) to analyze build logs and suggest fixes.
    Uses the AI Engine (Gemini) to analyze build logs.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)

        # Only analyze if we have logs
        if not deployment.build_logs:
            return {"status": "skipped", "reason": "no_build_logs"}

        # Call Jules AI
        agent = DevOpsAgent()

        # SECURITY: Sanitize logs to prevent prompt injection, then cap
        # to the last 15000 chars to bound token usage. Both layers
        # are needed: sanitization neutralizes injected instructions,
        # truncation bounds cost.
        safe_logs = _sanitize_for_llm(deployment.build_logs)[-15000:]

        diagnosis = agent.diagnose_logs(safe_logs)

        # Update deployment with AI insight
        deployment.ai_diagnosis = diagnosis
        deployment.save(update_fields=['ai_diagnosis'])
        return {"status": "ok", "deployment_id": str(deployment.id)}

    except Deployment.DoesNotExist:
        logger.warning("analyze_failure_task skipped: deployment %s not found", deployment_id)
        return {"status": "skipped", "reason": "deployment_not_found"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Error in analyze_failure_task for %s: %s", deployment_id, exc)
        return {"status": "error", "reason": str(exc)}
