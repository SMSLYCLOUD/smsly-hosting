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



@shared_task(bind=True, soft_time_limit=3600, time_limit=4200)
def execute_server_transfer_task(self, transfer_id):
    from .models_transfer import ServerTransfer as TransferModel
    from apps.deployments.services.transfer_service import ServerTransferService, _redact_transfer_text

    lock_key = f"server-transfer:{transfer_id}"
    if not cache.add(lock_key, "1", timeout=3600):
        logger.warning("Transfer Task: duplicate execution ignored for %s", transfer_id)
        return {"status": "skipped", "reason": "already_running"}

    try:
        transfer = TransferModel.objects.get(id=transfer_id)
    except TransferModel.DoesNotExist:
        logger.error("Transfer Task: transfer %s not found", transfer_id)
        cache.delete(lock_key)
        return {"status": "missing"}

    if transfer.status in {"COMPLETED", "FAILED", "ROLLED_BACK", "CANCELLED"}:
        cache.delete(lock_key)
        return {"status": "skipped", "reason": f"terminal:{transfer.status}"}

    try:
        engine = ServerTransferService(transfer)
        engine.execute()
        transfer.refresh_from_db(fields=["status"])
        if transfer.status == "COMPLETED":
            transfer.target_ssh_key = ""
            transfer.target_ssh_password = ""
            transfer.source_ssh_key = ""
            transfer.source_ssh_password = ""
            transfer.save(update_fields=[
                "target_ssh_key",
                "target_ssh_password",
                "source_ssh_key",
                "source_ssh_password",
            ])
        return {"status": transfer.status}
    except Exception as exc:
        logger.exception("Transfer Task: unhandled failure for %s: %s", transfer_id, exc)
        transfer.status = "FAILED"
        transfer.error_message = _redact_transfer_text(str(exc))[:4000]
        transfer.target_ssh_key = ""
        transfer.target_ssh_password = ""
        transfer.source_ssh_key = ""
        transfer.source_ssh_password = ""
        transfer.save(update_fields=[
            "status",
            "error_message",
            "target_ssh_key",
            "target_ssh_password",
            "source_ssh_key",
            "source_ssh_password",
        ])
        return {"status": "FAILED", "error": str(exc)}
    finally:
        cache.delete(lock_key)



@shared_task(bind=True)
def rollback_transfer_task(self, transfer_id):

    lock_key = f"server-transfer-rollback:{transfer_id}"
    if not cache.add(lock_key, "1", timeout=1800):
        logger.warning("Transfer Rollback Task: duplicate rollback ignored for %s", transfer_id)
        return {"status": "skipped", "reason": "already_running"}

    try:
        transfer = TransferModel.objects.get(id=transfer_id)
        if transfer.status in {"COMPLETED", "FAILED", "ROLLED_BACK", "CANCELLED"}:
            cache.delete(lock_key)
            return {"status": "skipped", "reason": f"terminal:{transfer.status}"}
        engine = ServerTransferService(transfer)
        engine.rollback()
        return {"status": "ROLLED_BACK"}
    except TransferModel.DoesNotExist:
        logger.error("Transfer Rollback Task: transfer %s not found", transfer_id)
        return {"status": "missing"}
    except Exception as exc:
        logger.exception("Transfer Rollback Task failed for %s: %s", transfer_id, exc)
        return {"status": "FAILED", "error": str(exc)}
    finally:
        cache.delete(lock_key)
