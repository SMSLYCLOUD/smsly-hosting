from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)
from celery import shared_task
from django.db import models as db_models

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM
from apps.core.tasks.coalesce import skip_if_recent
from apps.deployments.models import Service
from apps.autoscaler.models.replica import ServiceReplica

# AUTOSCALE_BATCH_SIZE: maximum services to process per cursor page.
# The periodic task walks all eligible services in batches to avoid
# a single long-running query holding locks or OOMing.
AUTOSCALE_BATCH_SIZE = 20

_IN_FLIGHT_DEPLOYMENT_STATUSES = (
    'QUEUED',
    'REVIEW',
    'BUILDING',
    'AWAITING_APPROVAL',
    'BACKUP_RUNNING',
    'MIGRATION_PLANNING',
    'MIGRATION_RUNNING',
    'DEPLOYING',
    'HEALTH_CHECK',
    'STAGED',
    'ROLLING_BACK',
)


def _has_in_flight_deployment(service) -> bool:
    """Return True when the service currently has an in-flight deployment."""
    from apps.deployments.models import Deployment
    return Deployment.objects.filter(
        service=service,
        status__in=_IN_FLIGHT_DEPLOYMENT_STATUSES,
    ).exists()


@shared_task(
    name='apps.autoscaler.services.tasks_autoscale.analyze_all_services_task',
    bind=True,
    ignore_result=True,
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
)
@skip_if_recent("coalesce:autoscale-analyze", TASK_TIME_LIMIT_MEDIUM[0])
def analyze_all_services_task(self) -> dict[str, int]:
    """Periodic task: analyze active services and auto-scale as needed.

    Uses an ``id__gt`` cursor so the batch of 20 never silently drops
    services when more than 20 are candidates. Delegates each
    per-service decision to ``analyze_and_scale_service`` so the
    test suite (which patches that name) and the ``ScalingViewSet``
    REST endpoint share the same code path.
    """
    from apps.deployments.models import Deployment

    analyzed = 0
    last_id = None
    while True:
        in_flight_service_ids = Deployment.objects.filter(
            status__in=_IN_FLIGHT_DEPLOYMENT_STATUSES,
        ).values_list('service_id', flat=True)
        qs = Service.objects.filter(
            status__in=['ACTIVE', 'RUNNING'],
        ).filter(
            db_models.Q(autoscale_enabled=True) | db_models.Q(autoscale_enabled__isnull=True),
            max_replicas__gt=1,
        ).exclude(
            id__in=in_flight_service_ids,
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
    if _has_in_flight_deployment(service):
        logger.debug("Auto-scale skipped for %s: deployment in flight", service.name)
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
    """Mark replicas stuck in SPAWNING/DRAINING/DESTROYING for > 5 minutes as DESTROYED.

    ``SPAWNING`` ages from ``created_at``; ``DRAINING`` and ``DESTROYING``
    age from the time they entered that state (tracked via cache timestamp)
    so healthy old replicas beginning a graceful drain are never killed early.
    """
    import time
    from datetime import timedelta
    from django.core.cache import cache as django_cache
    from django.utils import timezone

    now = timezone.now()
    now_ts = time.time()
    threshold = now - timedelta(seconds=_STUCK_SPAWN_THRESHOLD_SECONDS)
    stuck = list(ServiceReplica.objects.filter(
        status='SPAWNING',
        created_at__lt=threshold,
    ))
    for rep in ServiceReplica.objects.filter(status__in=['DRAINING', 'DESTROYING']):
        ts_key = f"autoscale:transitional:{rep.id}:{rep.status}"
        first_seen = django_cache.get(ts_key)
        if first_seen is None:
            django_cache.set(ts_key, now_ts, timeout=3600)
            continue
        try:
            if (now_ts - float(first_seen)) >= _STUCK_SPAWN_THRESHOLD_SECONDS:
                stuck.append(rep)
        except (TypeError, ValueError):
            django_cache.set(ts_key, now_ts, timeout=3600)
    count = len(stuck)
    if count > 0:
        logger.warning(
            "Cleaning up %d stuck transitional (SPAWNING/DRAINING/DESTROYING) replicas (older than %ds)",
            count, _STUCK_SPAWN_THRESHOLD_SECONDS,
        )
        # Destroy the actual containers, not just the DB rows: a
        # partially-spawned container keeps `--restart unless-stopped` and
        # the canonical Traefik labels, so a DB-only flip would leave it
        # running and silently serving traffic. Best-effort per replica —
        # one bad host must not stall the rest of the sweep, and a row
        # left SPAWNING is retried on the next beat.
        from apps.deployments.services.spawning_service import SpawningService
        from celery.exceptions import SoftTimeLimitExceeded
        spawner = SpawningService()
        try:
            for replica in stuck:
                try:
                    spawner.destroy(replica)
                except SoftTimeLimitExceeded:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Stuck-spawn destroy failed for %s: %s",
                        getattr(replica, 'container_name', '?'), exc,
                    )
        finally:
            spawner.cleanup()

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


_DEAD_RUNNING_GRACE_SECONDS = 900


@shared_task(
    name='apps.autoscaler.services.tasks_autoscale.cleanup_dead_running',
    bind=True,
    ignore_result=True,
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
)
def cleanup_dead_running(self) -> dict[str, int]:
    """Flip RUNNING replicas whose container is gone to DESTROYED.

    Replica counts trust the DB row, so a container removed out-of-band
    (manual `docker rm`, wiped remote host, failed HA failover deploy)
    leaves a phantom RUNNING row that inflates the count and can wedge
    the service at a false `at max_replicas`, blocking scale-up. Only a
    *confirmed absence* heals — uncertainty keeps the row (fail closed).
    Rows younger than the grace period are skipped: HA failover marks
    RUNNING before its deploy materializes the container, and normal
    deploys need minutes for large images.
    """
    from datetime import timedelta
    from django.utils import timezone

    from celery.exceptions import SoftTimeLimitExceeded

    healed = 0
    checked = 0
    try:
        candidates = list(ServiceReplica.objects.filter(
            status='RUNNING',
            created_at__lt=timezone.now() - timedelta(seconds=_DEAD_RUNNING_GRACE_SECONDS),
        ))
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.warning("Dead-running sweep: replica query failed: %s", exc)
        return {'healed': 0, 'checked': 0}
    if not candidates:
        return {'healed': 0, 'checked': 0}

    from apps.deployments.services.spawning_service import SpawningService
    spawner = SpawningService()
    try:
        for replica in candidates:
            checked += 1
            try:
                svc = getattr(replica, "service", None)
                if svc is not None and bool(getattr(svc, "sablier_enabled", False)):
                    continue
            except Exception:
                pass
            try:
                missing = spawner.replica_container_missing(replica)
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                logger.debug(
                    "Liveness probe skipped for %s: %s",
                    getattr(replica, 'container_name', '?'), exc,
                )
                continue
            if missing is True:
                logger.warning(
                    "Healing phantom RUNNING replica %s (container gone)",
                    getattr(replica, 'container_name', '?'),
                )
                try:
                    replica.status = 'DESTROYED'
                    replica.destroyed_at = timezone.now()
                    replica.save(update_fields=['status', 'destroyed_at'])
                    healed += 1
                except SoftTimeLimitExceeded:
                    raise
                except Exception as exc:
                    logger.debug("Phantom heal save failed: %s", exc)
    finally:
        spawner.cleanup()
    return {'healed': healed, 'checked': checked}


def build_vpa_resource_spec(service, ceiling: float | None = None) -> dict:
    """Compute consistent Docker VPA soft reservations + hard ceilings.

    Shared by ``apply_vpa_limits_task`` and ``ScalingViewSet.apply_vpa`` so
    CLI (``docker update``) and Docker SDK (``container.update``) apply
    identical CPU shares/quotas and memory burst ceilings.
    """
    if ceiling is None:
        try:
            ceiling = float(os.environ.get("VPA_CEILING_MULTIPLIER", "1.5"))
        except (TypeError, ValueError):
            ceiling = 1.5
    ceiling = max(1.0, float(ceiling))

    memory = int(service.memory_mb) if getattr(service, 'memory_mb', None) and service.memory_mb > 0 else 0
    try:
        cpu_cores = float(service.cpu_cores) if getattr(service, 'cpu_cores', None) and service.cpu_cores > 0 else 0.0
    except (TypeError, ValueError):
        cpu_cores = 0.0

    update_parts: list[str] = []
    docker_kwargs: dict = {}

    if memory > 0:
        mem_limit_mb = int(memory * ceiling)
        update_parts.append(f"--memory={mem_limit_mb}m")
        update_parts.append(f"--memory-reservation={memory}m")
        docker_kwargs['mem_reservation'] = f"{memory}m"
        docker_kwargs['mem_limit'] = f"{mem_limit_mb}m"

    if cpu_cores > 0.0:
        cpu_shares = max(2, int(round(cpu_cores * 1024)))
        cpu_period = 100000
        cpu_quota = max(1000, int(round(cpu_cores * cpu_period * ceiling)))
        update_parts.append(f"--cpu-shares={cpu_shares}")
        update_parts.append(f"--cpu-period={cpu_period}")
        update_parts.append(f"--cpu-quota={cpu_quota}")
        docker_kwargs['cpu_shares'] = cpu_shares
        docker_kwargs['cpu_period'] = cpu_period
        docker_kwargs['cpu_quota'] = cpu_quota

    return {
        'ceiling': ceiling,
        'update_parts': update_parts,
        'docker_kwargs': docker_kwargs,
    }


def service_vpa_target_containers(service) -> list[str]:
    """Return deduplicated primary + non-destroyed replica container names."""
    names = [service.name]
    try:
        names.extend(
            r.container_name for r in
            ServiceReplica.objects.filter(service=service).exclude(status="DESTROYED")
            if r.container_name
        )
    except Exception:
        pass
    return list(dict.fromkeys(names))


def service_vpa_targets_by_node(service) -> list[tuple[object | None, list[str]]]:
    """Group primary + non-destroyed replica containers by their target host.

    Primary ``ManagedServer(is_primary=True)`` and ``None`` both map to local
    Docker (``None`` key) so services pinned to the primary server row do not
    attempt remote SSH/API calls against the master itself, and overflow
    replicas run ``docker update`` against each replica's own ``replica.node``.
    """
    groups: dict[str, tuple[object | None, list[str]]] = {}

    def _add(node_obj, container_name: str) -> None:
        if not container_name:
            return
        is_local = node_obj is None or bool(getattr(node_obj, 'is_primary', False))
        effective_node = None if is_local else node_obj
        key = '__local__' if effective_node is None else str(getattr(effective_node, 'id', id(effective_node)))
        if key not in groups:
            groups[key] = (effective_node, [])
        if container_name not in groups[key][1]:
            groups[key][1].append(container_name)

    primary_server = getattr(service, 'server', None) if getattr(service, 'server_id', None) else None
    _add(primary_server, service.name)
    try:
        replicas = (
            ServiceReplica.objects.select_related('node')
            .filter(service=service)
            .exclude(status="DESTROYED")
        )
        for rep in replicas:
            rep_node = rep.node if getattr(rep, 'node_id', None) else primary_server
            _add(rep_node, rep.container_name)
    except Exception:
        pass
    return list(groups.values())


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

    Resolution order per target host:
    1. Local Docker SDK (when target node is None or ``is_primary=True``)
    2. SSH (if remote node has ssh_key or ssh_password stored)
    3. RemoteOrchestrator API call (for self-provisioned/bootstrap nodes)
    """
    import shlex
    import docker as docker_lib
    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
    from apps.deployments.services.ssh_client import SSHClient

    services = Service.objects.select_related('server').filter(
        vpa_enabled=True,
        status__in=['ACTIVE', 'RUNNING'],
    )
    updated = 0
    skipped = 0

    for service in services:
        try:
            spec = build_vpa_resource_spec(service)
            update_parts = spec['update_parts']
            docker_kwargs = spec['docker_kwargs']
            if not update_parts:
                continue

            for node, container_names in service_vpa_targets_by_node(service):
                if node is None:
                    # Local containers (primary + local replicas)
                    try:
                        client = docker_lib.from_env()
                        for target_name in container_names:
                            try:
                                container = client.containers.get(target_name)
                            except docker_lib.errors.NotFound:
                                continue
                            container.update(**docker_kwargs)
                            updated += 1
                    except Exception as exc:
                        logger.warning("VPA: local docker update failed for %s: %s", service.name, exc)
                    continue

                # Path 1: SSH on remote node
                if node.ssh_key or node.ssh_password:
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
                        for target_name in container_names:
                            cmd = f"docker update {' '.join(update_parts)} {shlex.quote(target_name)}"
                            _stdout, stderr, exit_code = ssh.exec_command(cmd, timeout=60)
                            if exit_code == 0:
                                updated += 1
                            else:
                                logger.warning("VPA: docker update failed for %s on %s: %s", target_name, node.name, stderr)
                    except Exception as exc:
                        logger.warning("VPA: SSH failed for %s on %s: %s", service.name, node.name, exc)
                    finally:
                        ssh.close()
                    continue

                # Path 2: RemoteOrchestrator (TLS + token/HMAC auth)
                if getattr(node, 'api_token', None) or getattr(node, 'gateway_secret', None):
                    try:
                        orch = RemoteOrchestrator(node)
                        resp = orch._request(
                            "POST",
                            f"/api/v1/autoscaler/services/{service.id}/apply_vpa/",
                            payload={
                                'service_name': service.name,
                                'containers': container_names,
                            },
                            timeout=30,
                        )
                        if resp is not None and resp.ok:
                            updated += len(container_names)
                        else:
                            detail = orch.describe_last_error() or (resp.text[:200] if resp is not None else 'no response')
                            logger.warning("VPA: node API failed for %s on %s: %s", service.name, node.name, detail)
                    except Exception as exc:
                        logger.warning("VPA: node API call failed for %s on %s: %s", service.name, node.name, exc)
                    continue

                # No way to reach the node
                logger.warning(
                    "VPA: skipping %s — node %s has no SSH credentials and no API token",
                    service.name, node.name,
                )
                skipped += len(container_names)

        except Exception as exc:
            logger.warning("apply_vpa_limits: failed for %s: %s", service.name, exc)

    return {'updated': updated, 'skipped': skipped}
