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

def _get_docker_client():
    """Get Docker client, return None if unavailable."""
    try:
        from apps.cloud.docker_client import get_docker_client
        return get_docker_client(timeout=5)
    except Exception as e:
        logger.debug("Docker SDK unavailable: %s", e)
        return None


def _collect_container_stats(container_id: str):
    """Collect real stats from a Docker container."""
    client = _get_docker_client()
    if not client or not container_id:
        return None

    try:
        container = client.containers.get(container_id)
        stats = container.stats(stream=False)

        # Parse CPU
        cpu_delta = (
            stats['cpu_stats']['cpu_usage']['total_usage']
            - stats['precpu_stats']['cpu_usage']['total_usage']
        )
        system_delta = (
            stats['cpu_stats']['system_cpu_usage']
            - stats['precpu_stats']['system_cpu_usage']
        )
        num_cpus = stats['cpu_stats'].get('online_cpus', 1)
        cpu_cores_used = 0
        if system_delta > 0:
            cpu_cores_used = (cpu_delta / system_delta) * num_cpus

        # Parse Memory
        mem_usage_bytes = stats['memory_stats'].get('usage', 0)
        mem_limit_bytes = stats['memory_stats'].get('limit', 0)
        mem_usage_mb = mem_usage_bytes // (1024 * 1024)
        mem_limit_mb = mem_limit_bytes // (1024 * 1024)

        # Parse Network
        networks = stats.get('networks', {})
        rx_bytes = sum(n.get('rx_bytes', 0) for n in networks.values())
        tx_bytes = sum(n.get('tx_bytes', 0) for n in networks.values())

        # Parse Disk I/O
        blkio = stats.get('blkio_stats', {}).get('io_service_bytes_recursive', []) or []
        read_bytes = sum(e['value'] for e in blkio if e.get('op') == 'read')
        write_bytes = sum(e['value'] for e in blkio if e.get('op') == 'write')

        return {
            'cpu_usage': round(cpu_cores_used, 4),
            'cpu_limit': num_cpus,
            'memory_usage': mem_usage_mb,
            'memory_limit': mem_limit_mb if mem_limit_mb > 0 else 512,
            'network_rx_bytes': rx_bytes,
            'network_tx_bytes': tx_bytes,
            'disk_read_bytes': read_bytes,
            'disk_write_bytes': write_bytes,
        }
    except Exception as e:
        logger.debug("Failed to get stats for container %s: %s", container_id, e)
        return None


def _simulate_stats(service):
    """Generate simulated metrics when Docker stats are unavailable."""
    cpu_limit = float(service.cpu_cores)
    mem_limit = service.memory_mb
    return {
        'cpu_usage': round(cpu_limit * random.uniform(0.05, 0.65), 4),
        'cpu_limit': cpu_limit,
        'memory_usage': int(mem_limit * random.uniform(0.15, 0.70)),
        'memory_limit': mem_limit,
        'network_rx_bytes': random.randint(1000, 500000),
        'network_tx_bytes': random.randint(500, 250000),
        'disk_read_bytes': random.randint(0, 100000),
        'disk_write_bytes': random.randint(0, 50000),
    }


@shared_task
def collect_metrics_task():
    """
    Collect metrics for all active services with running deployments.
    Tries real Docker stats first, falls back to simulation.
    """
    now = timezone.now()
    services = Service.objects.all()
    collected = 0

    for service in services:
        # Find latest active deployment to get container_id
        latest = (
            Deployment.objects.filter(
                service=service, status=Deployment.Status.ACTIVE
            ).order_by('-created_at').first()
        )
        container_id = getattr(latest, 'container_id', None) if latest else None

        # Only store real Docker stats — never synthetic data.
        # The live Docker fallback in the metrics API handles the case
        # where Docker is available but Prometheus is not. Synthetic data
        # would mislead dashboards and alerting.
        stats = _collect_container_stats(container_id)
        if stats is None:
            continue

        ServiceMetric.objects.create(
            service=service,
            timestamp=now,
            **stats,
        )
        collected += 1

        # Check resource thresholds and fire alerts
        _check_metric_thresholds(service, stats, now)

        # Record usage for billing
        try:
            from apps.billing.services.metering import UsageMeter
            meter = UsageMeter()
            cpu_limit = float(stats.get('cpu_limit') or 1.0)
            cpu_usage = float(stats.get('cpu_usage') or 0.0)
            cpu_pct = (cpu_usage / cpu_limit * 100.0) if cpu_limit > 0 else 0.0
            mem_mb = float(stats.get('memory_usage') or 0.0)
            if cpu_pct > 0:
                meter.record_usage(service.owner, 'cpu_hours', cpu_pct / 100, timestamp=now)
            if mem_mb > 0:
                meter.record_usage(service.owner, 'memory_gb_hours', mem_mb / 1024, timestamp=now)
        except Exception:
            pass

    # Prune old metrics (keep 7 days)
    cutoff = now - timezone.timedelta(days=7)
    deleted, _ = ServiceMetric.objects.filter(timestamp__lt=cutoff).delete()
    if deleted:
        logger.info("Pruned %d old metric records", deleted)

    logger.info("Collected metrics for %d services", collected)


@shared_task
def cleanup_build_cache_task():
    """Clean up Docker build cache to free disk space."""
    client = _get_docker_client()
    if not client:
        logger.info("Docker unavailable, skipping build cache cleanup")
        return

    try:
        result = client.api.prune_builds(filters={'until': '72h'})
        reclaimed = result.get('SpaceReclaimed', 0) // (1024 * 1024)
        logger.info("Build cache cleanup: reclaimed %d MB", reclaimed)
    except Exception as e:
        logger.warning("Build cache cleanup failed: %s", e)


def _check_metric_thresholds(service, stats, now):
    """Fire resource alerts when metrics breach thresholds."""
    try:
        cpu_limit = float(stats.get('cpu_limit') or 1.0)
        cpu_usage = float(stats.get('cpu_usage') or 0.0)
        cpu = (cpu_usage / cpu_limit * 100.0) if cpu_limit > 0 else 0.0
        mem_mb = float(stats.get('memory_usage') or 0.0)
        disk_pct = stats.get('disk_percent', 0)

        alerts = []
        if cpu > 90:
            alerts.append(f"CPU at {cpu:.0f}%")
        if mem_mb > 0 and mem_mb > 2000:
            alerts.append(f"Memory at {mem_mb:.0f}MB")
        if disk_pct > 90:
            alerts.append(f"Disk at {disk_pct:.0f}%")

        if alerts:
            from apps.notifications.tasks import notify_health_alert
            cache_key = f"metrics_alert:{service.id}:{now.strftime('%Y%m%d%H')}"
            from django.core.cache import cache
            if cache.get(cache_key):
                return
            cache.set(cache_key, 1, 3600)  # 1 hour dedup

            notify_health_alert.delay(
                str(service.id),
                severity='WARNING',
                message='; '.join(alerts),
            )
    except Exception:
        pass
