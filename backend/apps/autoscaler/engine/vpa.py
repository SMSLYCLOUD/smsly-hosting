"""Vertical Pod Autoscaling — adjust CPU/RAM on the existing container.

When VPA is enabled, the autoscaler increases the resource limits on the
running container instead of spawning replicas. This is useful for
single-instance services that need more resources without replication.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

VPA_CPU_STEP = 0.5
VPA_MEM_STEP_MB = 512
VPA_CPU_MAX = 8.0
VPA_MEM_MAX_MB = 16384
VPA_CPU_MIN = 0.25
VPA_MEM_MIN_MB = 256


def apply_vpa(service, metrics, recommendation, *, now=None) -> bool:
    """Adjust the container's CPU/RAM limits based on the scaling recommendation.

    Returns True if the adjustment was applied, False if it was skipped.
    """
    now = now or timezone.now()

    try:
        import docker as docker_lib
        client = docker_lib.from_env()
    except Exception as exc:
        logger.warning("VPA: Docker client unavailable: %s", exc)
        return False

    container_id = service.container_id or service.green_container_id
    if not container_id:
        logger.info("VPA: No container found for service %s — skipping", service.name)
        return False

    try:
        container = client.containers.get(container_id)
    except Exception:
        logger.info("VPA: Container %s not found for %s", container_id[:12], service.name)
        return False

    from decimal import Decimal

    current_cpu = float(service.cpu_cores or 1.0)
    current_mem = int(service.memory_mb or 2048)

    cpu_percent = metrics.cpu_percent or 0.0
    mem_mb = metrics.memory_mb or current_mem

    new_cpu = min(current_cpu + VPA_CPU_STEP, VPA_CPU_MAX)
    new_mem = min(current_mem + VPA_MEM_STEP_MB, VPA_MEM_MAX_MB)

    if new_cpu == current_cpu and new_mem == current_mem:
        logger.info("VPA: %s already at max resources (cpu=%.1f, mem=%dMB)", service.name, current_cpu, current_mem)
        return False

    try:
        container.update(
            mem_limit=f"{new_mem}m",
            nano_cpus=int(new_cpu * 1e9),
        )
    except Exception as exc:
        logger.warning("VPA: Failed to update container %s: %s", container_id[:12], exc)
        return False

    service.cpu_cores = Decimal(str(new_cpu))
    service.memory_mb = new_mem
    service.last_scale_at = now
    service.save(update_fields=['cpu_cores', 'memory_mb', 'last_scale_at'])

    logger.info(
        "VPA: Adjusted %s — cpu %.1f→%.1f cores, mem %d→%dMB (CPU was %.0f%%)",
        service.name, current_cpu, new_cpu, current_mem, new_mem, cpu_percent,
    )
    return True
