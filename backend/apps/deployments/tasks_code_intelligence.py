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

@shared_task(bind=True)
def deep_scan_and_verify_task(self, user_id, repos_data, deploy_plan, ai_provider=None):
    """
    Background task to perform a deep codebase scan and cross-verify with the deployment plan.
    """
    try:
        user = User.objects.get(id=user_id)

        try:
            from apps.deployments.models import Service
            owned_repo_ids = set(
                str(sid) for sid in Service.objects.filter(
                    owner=user
                ).values_list('id', flat=True)
            )
            owned_repo_urls = set(
                url for url in Service.objects.filter(
                    owner=user
                ).values_list('repository_url', flat=True)
                if url
            )
        except Exception as e:
            logger.error("Could not load ownership for %s: %s", user_id, e)
            return {"error": f"ownership check failed: {e}"}

        safe_repos = []
        for repo in repos_data:
            if not isinstance(repo, dict):
                continue
            owner_id = repo.get('owner_id')
            if owner_id and owner_id != user_id:
                logger.warning(
                    "Dropping repo %s from deep scan: not owned by user %s",
                    repo.get('id') or repo.get('repo'),
                    user_id,
                )
                continue
            if owner_id is None:
                repo_id = str(repo.get('id') or repo.get('repo_id') or '')
                repo_url = repo.get('repo') or repo.get('html_url') or repo.get('url') or ''
                owned = False
                if repo_id and repo_id in owned_repo_ids:
                    owned = True
                elif repo_url and repo_url in owned_repo_urls:
                    owned = True
                if not owned:
                    logger.warning(
                        "Dropping unowned repo %s from deep scan for user %s",
                        repo_id or repo_url,
                        user_id,
                    )
                    continue
            safe_repos.append(repo)

        if not safe_repos:
            logger.info("deep_scan_and_verify_task: no owned repos for user %s; skipping LLM call", user_id)
            return {
                "global_overview": "",
                "verification": {
                    "is_valid": True,
                    "missing_env_vars": [],
                    "architectural_warnings": [],
                    "skipped": True,
                },
            }

        github_token = None
        try:
            from apps.deployments.views_analysis import RepoAnalysisView
            view = RepoAnalysisView()
            github_token = view._get_github_access_token(user)
        except Exception as e:
            logger.warning(f"Could not retrieve github token: {e}")

        self.update_state(state='PROGRESS', meta={'state': 'Starting deep codebase scan...'})

        result = analyze_codebase_chunked(
            repos_data=safe_repos,
            deploy_plan=deploy_plan,
            github_token=github_token,
            ai_provider=ai_provider
        )

        return result

    except Exception as e:
        logger.error(f"Deep scan task failed: {str(e)}", exc_info=True)
        return {"error": str(e)}
