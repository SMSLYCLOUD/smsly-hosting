"""Real Docker stats collection task.

Collects CPU, memory, network, and disk metrics from running containers
via the Docker SDK. Falls back to simulated data if Docker is unreachable.
"""
import logging
import random
from celery import shared_task
from django.utils import timezone
from .models import Service, Deployment
from .models_metrics import ServiceMetric

logger = logging.getLogger(__name__)


def _get_docker_client():
    """Get Docker client, return None if unavailable."""
    try:
        import docker
        return docker.from_env(timeout=5)
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

        # Try real stats, fall back to simulated
        stats = _collect_container_stats(container_id)
        if stats is None:
            stats = _simulate_stats(service)

        ServiceMetric.objects.create(
            service=service,
            timestamp=now,
            **stats,
        )
        collected += 1

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
