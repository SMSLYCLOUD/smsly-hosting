import logging
logger = logging.getLogger(__name__)
import hashlib
import hmac
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



@shared_task(name="apps.deployments.tasks.auto_authenticate_nodes_task")
def auto_authenticate_nodes_task():
    """
    Periodic task to automatically repair inter-node authentication.

    Checks for ManagedServer records missing API tokens and attempts to
    retrieve them via SSH using RemoteOrchestrator.
    """
    from apps.deployments.models import ManagedServer
    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator

    # Target nodes missing tokens but having SSH access
    servers = ManagedServer.objects.filter(api_token='')
    count = 0
    for server in servers:
        if server.ssh_key or server.ssh_password:
            try:
                logger.info("Auto-Auth Task: Attempting SSH retrieval for %s", server.host)
                orch = RemoteOrchestrator(server)
                if orch.auto_authenticate():
                    count += 1
            except Exception as e:
                logger.warning("Auto-Auth Task failed for %s: %s", server.host, e)

    if count > 0:
        logger.info("Auto-Auth Task completed: Fixed %d node(s)", count)
    return count



@shared_task(name="apps.deployments.tasks.check_managed_servers_health_task")
def check_managed_servers_health_task():
    """
    Periodic task (every 5 min) to check health of all managed servers.
    Updates ManagedServer.status to ONLINE or OFFLINE based on /health response.
    """
    from apps.deployments.models_servers import ManagedServer
    from apps.deployments.views_servers import _refresh_managed_server_health

    servers = ManagedServer.objects.exclude(
        provision_status__in=("pending", "provisioning", "failed")
    )
    checked = 0
    for server in servers:
        try:
            _refresh_managed_server_health(server)
            checked += 1
        except Exception as exc:
            logger.warning("Health check failed for %s (%s): %s", server.name, server.host, exc)

    # Refresh Prometheus target files. Agent deployment (docker-labels, Promtail,
    # cAdvisor, Node Exporter) is handled by node_watchdog_task to avoid redundant
    # SSH connections per cycle.
    try:
        from apps.deployments.services.prometheus_targets import (
            write_docker_labels_targets,
        )
        write_docker_labels_targets()
    except Exception as exc:
        logger.debug("Prometheus target update skipped: %s", exc)

    if checked:
        logger.info("Health check task: refreshed %d/%d servers", checked, servers.count())
    return checked



@shared_task(bind=True, max_retries=0, soft_time_limit=300, time_limit=330)
def node_watchdog_task(self):
    """
    Periodic watchdog that checks all managed servers for health issues.

    For each server:
    1. Checks SSH connectivity
    2. Checks Docker daemon status
    3. Checks disk and memory usage
    4. Attempts auto-recovery for critical issues
    5. Updates server status in the database

    Runs every 5 minutes via Celery beat.
    """
    # Update Prometheus target files for docker-labels exporters
    try:
        from apps.deployments.services.prometheus_targets import (
            write_docker_labels_targets,
        )
        write_docker_labels_targets()
    except Exception as exc:
        logger.warning("Failed to update Prometheus targets: %s", exc)
    try:
        from apps.deployments.models_core import ManagedServer
        from apps.deployments.services.self_healing_orchestrator import (
            SelfHealingOrchestrator,
            FailureType,
        )
    except ImportError:
        logger.warning("Self-healing modules not available — watchdog skipped")
        return

    servers = ManagedServer.objects.filter(
        is_primary=False,
        status=ManagedServer.Status.ONLINE,
    )

    results = {"checked": 0, "healed": 0, "failed": 0, "offline": 0}

    for server in servers:
        try:
            results["checked"] += 1

            if not server.ssh_key and not server.ssh_password:
                logger.debug("Skipping %s — no SSH credentials", server.name)
                continue

            orchestrator = SelfHealingOrchestrator(server)
            diagnostics = orchestrator.run_full_diagnostics()

            old_status = server.status
            if diagnostics.docker_running and diagnostics.network_reachable:
                server.status = ManagedServer.Status.ONLINE
            else:
                server.status = ManagedServer.Status.OFFLINE

            server.last_health_check = timezone.now()
            server.save(update_fields=["status", "last_health_check", "updated_at"])

            # Auto-deploy docker-labels exporter on online nodes
            if server.status == ManagedServer.Status.ONLINE:
                try:
                    from apps.deployments.services.prometheus_targets import (
                        deploy_docker_labels_exporter_on_node,
                        deploy_promtail_on_node,
                        deploy_cadvisor_on_node,
                        deploy_node_exporter_on_node,
                    )
                    deploy_docker_labels_exporter_on_node(server)
                    deploy_promtail_on_node(server)
                    deploy_cadvisor_on_node(server)
                    deploy_node_exporter_on_node(server)
                except Exception as exc:
                    logger.debug("docker-labels/promtail deploy skipped for %s: %s", server.name, exc)

            if diagnostics.docker_running and old_status != ManagedServer.Status.ONLINE:
                logger.info("Server %s recovered — status: ONLINE", server.name)

            if not diagnostics.docker_running:
                logger.warning("Server %s — Docker daemon down, attempting recovery", server.name)
                results["offline"] += 1

                heal_result = orchestrator.heal_deployment_failure(
                    type("obj", (object,), {"id": "watchdog", "container_id": "", "service": type("o", (object,), {"name": ""})()})()
                )
                if heal_result.success:
                    results["healed"] += 1
                    server.status = ManagedServer.Status.ONLINE
                    server.save(update_fields=["status", "updated_at"])
                    logger.info("Server %s healed via watchdog", server.name)

            elif diagnostics.failure_type == FailureType.DISK_FULL:
                logger.warning("Server %s — disk full, pruning images", server.name)
                heal_result = orchestrator._execute_recovery(
                    type("obj", (object,), {"value": "prune_images"})(),
                    type("obj", (object,), {"id": "watchdog", "container_id": "", "service": type("o", (object,), {"name": ""})()})(),
                    diagnostics,
                )
                if heal_result.success:
                    results["healed"] += 1
                    logger.info("Server %s disk space recovered via watchdog", server.name)

            orchestrator._close_ssh()

        except Exception as exc:
            results["failed"] += 1
            logger.warning("Watchdog check failed for %s: %s", server.name, exc)
            try:
                server.status = ManagedServer.Status.OFFLINE
                server.last_health_check = timezone.now()
                server.save(update_fields=["status", "last_health_check", "updated_at"])
            except Exception:
                pass

    logger.info(
        "Node watchdog complete: checked=%d healed=%d failed=%d offline=%d",
        results["checked"], results["healed"], results["failed"], results["offline"],
    )
    return results



@shared_task(bind=True, max_retries=2)
def refresh_managed_server_health(self, server_id: str):
    """Refresh the health/status of a single managed server."""
    from .models_servers import ManagedServer
    from .views_servers import _refresh_managed_server_health
    try:
        server = ManagedServer.objects.get(id=server_id)
        _refresh_managed_server_health(server)
    except ManagedServer.DoesNotExist:
        logger.warning("refresh_managed_server_health: server %s not found", server_id)
    except Exception as exc:
        logger.exception("refresh_managed_server_health failed for %s: %s", server_id, exc)



@shared_task(soft_time_limit=600, time_limit=900)
def sync_master_db_to_agents_task():
    """
    Periodically push a compressed pg_dump of the master database to all
    connected lite agents. Enables disaster recovery: if the master goes
    down, any agent's backup can restore the database on a replacement master.

    Runs every 6 hours via Celery beat.
    """
    import subprocess
    import tempfile
    import shutil

    from .models_servers import ManagedServer
    from django.conf import settings

    agents = ManagedServer.objects.filter(
        is_lite_agent=True,
        status=ManagedServer.Status.ONLINE,
    )
    if not agents.exists():
        logger.info("sync_master_db_to_agents: no lite agents connected — skipping")
        return

    db_url = getattr(settings, 'DATABASE_URL', '')
    if not db_url:
        logger.warning("sync_master_db_to_agents: DATABASE_URL not configured — skipping")
        return

    tmp_dir = tempfile.mkdtemp(prefix='master_db_sync_')
    dump_path = os.path.join(tmp_dir, 'master_db.sql.gz')

    try:
        # Create compressed pg_dump
        result = subprocess.run(
            ['pg_dump', db_url, '--no-owner', '--no-acl', '-Z', '9', '-f', dump_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.error("sync_master_db_to_agents: pg_dump failed: %s", result.stderr[:500])
            return

        file_size = os.path.getsize(dump_path)
        logger.info("sync_master_db_to_agents: dump created (%.1f MB), pushing to %d agents",
                    file_size / (1024 * 1024), agents.count())

        # Push to each lite agent via REST API
        # Send raw binary in body (not multipart) so body_hash computed
        # from file content matches request.body on the receiving end.
        with open(dump_path, 'rb') as f_body:
            raw_body_bytes = f_body.read()
        body_hash = hashlib.sha256(raw_body_bytes).hexdigest()

        for agent in agents:
            target_ip = agent.wg_address or agent.private_ip or agent.host
            if not target_ip:
                continue
            url = f"http://{target_ip}/api/v1/transfers/incoming/db-backup/"
            secret = str(getattr(settings, 'GATEWAY_SECRET', '') or getattr(settings, 'SECRET_KEY', ''))
            timestamp = str(int(time.time()))
            nonce = secrets.token_hex(16)

            raw_sig = f"POST|/api/v1/transfers/incoming/db-backup/|{timestamp}|{nonce}|{body_hash}"
            signature = hmac.new(secret.encode(), raw_sig.encode(), hashlib.sha256).hexdigest()

            try:
                resp = requests.post(
                    url,
                    data=raw_body_bytes,
                    headers={
                        'X-Gateway-Signature-V2': signature,
                        'X-Request-Timestamp': timestamp,
                        'X-Request-Nonce': nonce,
                        'Content-Type': 'application/gzip',
                    },
                    timeout=600,
                )
                if resp.ok:
                    logger.info("sync_master_db_to_agents: pushed to agent %s (%s)", agent.name, target_ip)
                else:
                    logger.warning("sync_master_db_to_agents: agent %s returned %s", agent.name, resp.status_code)
            except requests.RequestException as e:
                logger.warning("sync_master_db_to_agents: failed to push to agent %s: %s", agent.name, e)

    except subprocess.TimeoutExpired:
        logger.error("sync_master_db_to_agents: pg_dump timed out")
    except Exception as e:
        logger.error("sync_master_db_to_agents: failed: %s", e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
