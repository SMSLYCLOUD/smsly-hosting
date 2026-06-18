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

def _get_local_role() -> str:
    """Read local server role from file."""
    try:
        with open("/tmp/.smsly_cluster_role", "r") as f:
            return f.read().strip()
    except Exception:
        return "FOLLOWER"


@shared_task(name="apps.deployments.tasks_election.heartbeat_task")
def heartbeat_task():
    """
    Periodic task (every 5s):
    - If LEADER: send heartbeats to all followers
    - If FOLLOWER: check if leader heartbeat has timed out
    - If CANDIDATE: do nothing (election in progress)
    """
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.election_service import ElectionService

    # Find active meshes with cluster state
    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        try:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
        except Exception as e:
            logger.error(f"Failed to get cluster for mesh {mesh.name}: {e}")
            continue

        role = _get_local_role()

        if role == "LEADER":
            try:
                ElectionService.send_heartbeat(cluster)
            except Exception as e:
                logger.error(f"Heartbeat send failed: {e}")

        elif role == "FOLLOWER":
            try:
                elected = ElectionService.check_leader_timeout(cluster)
                if elected:
                    logger.info("Election triggered — promoted to leader!")
            except Exception as e:
                logger.error(f"Leader timeout check failed: {e}")

        # Periodic cleanup of old heartbeat logs (every ~100 runs ≈ 8 min)
        import random
        if random.randint(1, 100) == 1:
            try:
                ElectionService.cleanup_old_heartbeats(cluster)
            except Exception:
                pass


@shared_task(name="apps.deployments.tasks_election.force_election_task")
def force_election_task(mesh_id: str = None):
    """
    Force a new election (admin action).

    Used when the current leader is misbehaving but hasn't technically
    timed out yet.
    """
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.election_service import ElectionService

    if mesh_id:
        try:
            mesh = MeshNetwork.objects.get(id=mesh_id)
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
            result = ElectionService.start_election(cluster)
            logger.info(f"Forced election for mesh {mesh.name}: {'won' if result else 'lost'}")
            return result
        except MeshNetwork.DoesNotExist:
            logger.error(f"Mesh {mesh_id} not found")
            return False
    else:
        # Election for all active meshes
        meshes = MeshNetwork.objects.filter(is_active=True)
        results = {}
        for mesh in meshes:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
            results[mesh.name] = ElectionService.start_election(cluster)
        return results
