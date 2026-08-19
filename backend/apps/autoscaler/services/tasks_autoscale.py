from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)
from celery import shared_task
from django.db import models as db_models

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM
from apps.deployments.models import Service
from apps.autoscaler.models.replica import ServiceReplica

# AUTOSCALE_BATCH_SIZE: maximum services to process per cursor page.
# The periodic task walks all eligible services in batches to avoid
# a single long-running query holding locks or OOMing.
AUTOSCALE_BATCH_SIZE = 20

@shared_task(
    name='apps.autoscaler.services.tasks_autoscale.analyze_all_services_task',
    bind=True,
    ignore_result=True,
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
)
def analyze_all_services_task(self) -> dict[str, int]:
    """Periodic task: analyze active services and auto-scale as needed.

    Uses an ``id__gt`` cursor so the batch of 20 never silently drops
    services when more than 20 are candidates. Delegates each
    per-service decision to ``analyze_and_scale_service`` so the
    test suite (which patches that name) and the ``ScalingViewSet``
    REST endpoint share the same code path.
    """
    analyzed = 0
    last_id = None
    while True:
        qs = Service.objects.filter(
            status='ACTIVE',
        ).filter(
            db_models.Q(autoscale_enabled=True) | db_models.Q(autoscale_enabled__isnull=True),
            max_replicas__gt=1,
        ).distinct()
        if last_id is not None:
            qs = qs.filter(id__gt=last_id)
        batch = list(qs.order_by('id')[:AUTOSCALE_BATCH_SIZE])
        if not batch:
            break
        for svc in batch:
            try:
                analyze_and_scale_service(str(svc.id))
                analyzed += 1
            except Exception as exc:
                logger.warning("Auto-scale failed for %s: %s", svc.name, exc)
        last_id = batch[-1].id
    return {'analyzed': analyzed}


def analyze_and_scale_service(service_id) -> dict[str, object] | None:
    """Public entry point used by the Celery task, REST endpoint, and tests.

    Accepts a ``Service`` UUID string (from the Celery task / test mocks)
    or a ``Service`` instance (from the REST view). Delegates to the
    unified engine pipeline.
    """
    from apps.autoscaler.engine.pipeline import analyze_and_apply

    if isinstance(service_id, Service):
        service = service_id
    else:
        try:
            service = Service.objects.get(id=service_id)
        except (Service.DoesNotExist, ValueError, TypeError):
            logger.warning("Auto-scale task: service %s not found", service_id)
            return None
    # Pass dedup window so the 3-min sweep and the 30s quick-check
    # share the same cache key and never race on the same service.
    return analyze_and_apply(service, min_interval_seconds=120)


_STUCK_SPAWN_THRESHOLD_SECONDS = 300


@shared_task(
    name='apps.autoscaler.services.tasks_autoscale.cleanup_stuck_spawning',
    bind=True,
    ignore_result=True,
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
)
def cleanup_stuck_spawning(self) -> dict[str, int]:
    """Mark replicas stuck in SPAWNING for > 5 minutes as DESTROYED.

    Without this, a failed spawn leaves the replica in SPAWNING forever,
    which blocks all future scaling for that service (spawning_in_progress
    check in the pipeline always returns True).
    """
    from datetime import timedelta
    from django.utils import timezone

    threshold = timezone.now() - timedelta(seconds=_STUCK_SPAWN_THRESHOLD_SECONDS)
    stuck = ServiceReplica.objects.filter(
        status='SPAWNING',
        created_at__lt=threshold,
    )
    count = stuck.count()
    if count > 0:
        logger.warning("Cleaning up %d stuck SPAWNING replicas (older than %ds)", count, _STUCK_SPAWN_THRESHOLD_SECONDS)
        stuck.update(status='DESTROYED', destroyed_at=timezone.now())

    # Also delete old DESTROYED replicas (> 24h) to prevent table bloat
    old_threshold = timezone.now() - timedelta(hours=24)
    old_destroyed = ServiceReplica.objects.filter(
        status='DESTROYED',
        destroyed_at__lt=old_threshold,
    )
    old_count = old_destroyed.count()
    if old_count > 0:
        logger.info("Deleting %d old DESTROYED replicas (>24h)", old_count)
        old_destroyed.delete()

    return {'cleaned': count, 'old_deleted': old_count}


@shared_task(
    name='apps.autoscaler.services.tasks_autoscale.apply_vpa_limits_task',
    bind=True,
    ignore_result=True,
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
)
def apply_vpa_limits_task(self) -> dict[str, int]:
    """Periodic task: apply VPA soft limits + hard ceiling to running containers.

    For each ``vpa_enabled=True`` service, updates the running Docker container
    to use soft reservations (``mem_reservation`` / ``cpu_shares``) plus a hard
    ceiling (``mem_limit`` / ``cpu_quota``) so the service can burst within a
    safe bound without starving neighbors.

    Handles both local containers (master) and remote nodes (via SSH).
    Auth method is determined by the ManagedServer's stored credentials:
    - SSH key (preferred, stored encrypted)
    - SSH password (fallback, may be cleared after provisioning)
    """
    import docker as docker_lib
    from apps.deployments.services.ssh_client import SSHClient

    try:
        ceiling = float(os.environ.get("VPA_CEILING_MULTIPLIER", "1.5"))
    except (TypeError, ValueError):
        ceiling = 1.5
    ceiling = max(1.0, ceiling)

    services = Service.objects.select_related('server').filter(vpa_enabled=True)
    updated = 0
    skipped = 0

    for service in services:
        try:
            memory = service.memory_mb
            cpu = int(service.cpu_cores * 1024) if service.cpu_cores else 0

            # Build the docker update command
            update_parts = []
            if memory and memory > 0:
                update_parts.append(f"--memory={memory}m")
                update_parts.append(f"--memory-reservation={memory}m")
            if cpu and cpu > 0:
                cpu_shares = max(2, int((cpu / 1000) * 1024))
                cpu_quota = int((cpu / 1000) * 100000 * ceiling)
                update_parts.append(f"--cpu-shares={cpu_shares}")
                update_parts.append("--cpu-period=100000")
                update_parts.append(f"--cpu-quota={cpu_quota}")
            if not update_parts:
                continue

            update_cmd = " ".join(update_parts)
            container_name = service.name

            if service.server_id:
                # Remote node: SSH in and run docker update
                node = service.server
                if not node.ssh_key and not node.ssh_password:
                    logger.warning(
                        "VPA: skipping %s — node %s has no SSH credentials "
                        "(add SSH key or password to ManagedServer record)",
                        service.name, node.name,
                    )
                    skipped += 1
                    continue

                ssh = SSHClient(
                    ip=node.host,
                    key_content=node.ssh_key,
                    password=node.ssh_password,
                    user=getattr(node, 'ssh_user', 'root') or 'root',
                    port=getattr(node, 'ssh_port', 22) or 22,
                    key_passphrase=getattr(node, 'ssh_key_passphrase', '') or '',
                    wg_address=getattr(node, 'wg_address', '') or '',
                )
                try:
                    cmd = f"docker update {update_cmd} {container_name}"
                    stdout, stderr, exit_code = ssh.exec_command(cmd, timeout=60)
                    if exit_code == 0:
                        updated += 1
                    else:
                        logger.warning(
                            "VPA: docker update failed for %s on %s: %s",
                            service.name, node.name, stderr,
                        )
                except Exception as exc:
                    logger.warning("VPA: SSH failed for %s on %s: %s", service.name, node.name, exc)
                finally:
                    ssh.close()
            else:
                # Local container: use Docker SDK directly
                try:
                    client = docker_lib.from_env()
                    container = client.containers.get(container_name)
                    update_kwargs = {}
                    if memory and memory > 0:
                        update_kwargs['mem_reservation'] = f"{memory}m"
                        update_kwargs['mem_limit'] = f"{int(memory * ceiling)}m"
                    if cpu and cpu > 0:
                        update_kwargs['cpu_shares'] = max(2, int((cpu / 1000) * 1024))
                        update_kwargs['cpu_period'] = 100000
                        update_kwargs['cpu_quota'] = int((cpu / 1000) * 100000 * ceiling)
                    container.update(**update_kwargs)
                    updated += 1
                except docker_lib.errors.NotFound:
                    pass

        except Exception as exc:
            logger.warning("apply_vpa_limits: failed for %s: %s", service.name, exc)

    return {'updated': updated, 'skipped': skipped}
