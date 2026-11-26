from __future__ import annotations

import logging
import os
import random
import time

logger = logging.getLogger(__name__)

from celery import shared_task

from apps.deployments.constants import BUILD_CACHE_MAX_AGE_HOURS, TASK_TIME_LIMIT_STANDARD
from apps.core.tasks.coalesce import skip_if_recent
from django.utils import timezone

from apps.deployments.models import (
    Deployment,
    Service,
)
from apps.autoscaler.models.metrics import ServiceMetric


def _get_docker_client(timeout=5):
    """Get Docker client, return None if unavailable."""
    try:
        from apps.cloud.docker_client import get_docker_client
        return get_docker_client(timeout=timeout)
    except Exception as e:
        logger.debug("Docker SDK unavailable: %s", e)
        return None


def _collect_container_stats(container_id: str, cpu_limit_override=None):
    """Collect real stats from a Docker container.

    ``cpu_limit_override`` should be the service's allocated cores: the
    daemon reports host-wide CPU counts, and dividing service usage by
    host cores understates CPU% (e.g. 3-core service on an 8-core host
    can never read above 37.5%, so CPU autoscaling never fires).
    Falls back to the host count when unset.
    """
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
        try:
            cpu_limit = float(cpu_limit_override or 0) or float(num_cpus)
        except (TypeError, ValueError):
            cpu_limit = float(num_cpus)

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
            'cpu_limit': cpu_limit,
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


def _collect_remote_container_stats(service, latest):
    """Remote equivalent of _collect_container_stats (units: MB, cores).

    Node returns mem_usage_mb / mem_limit_mb (see node_exec container_stats),
    matching local MB convention — no byte conversion here.
    """
    try:
        from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
        from apps.deployments.utils.target import resolve_active_execution_target
        target = resolve_active_execution_target(service)
        if target.get("target_type") not in ("remote", "lite_agent"):
            return None
        server = target.get("server_obj")
        if server is None:
            return None
        ref = (
            (getattr(latest, "verified_runtime_id", "") or "").strip()
            or (getattr(service, "active_runtime_id", "") or "").strip()
            or (getattr(latest, "container_id", "") or "").strip()
            or service.name
        )
        data = RemoteOrchestrator(server).get_container_stats(ref)
        if not data or data.get("cpu_usage") is None:
            return None
        try:
            cpu_limit = float(service.cpu_cores or data.get("cpu_limit") or 1)
        except (TypeError, ValueError):
            cpu_limit = float(data.get("cpu_limit") or 1)
        return {
            "cpu_usage": float(data.get("cpu_usage") or 0),
            "cpu_limit": cpu_limit,
            "memory_usage": int(data.get("memory_usage") or 0),
            "memory_limit": int(data.get("memory_limit") or service.memory_mb or 512),
            "network_rx_bytes": int(data.get("network_rx_bytes") or 0),
            "network_tx_bytes": int(data.get("network_tx_bytes") or 0),
            "disk_read_bytes": int(data.get("disk_read_bytes") or 0),
            "disk_write_bytes": int(data.get("disk_write_bytes") or 0),
        }
    except ValueError:
        return None
    except Exception as exc:
        logger.debug("Remote stats collection failed for %s: %s", getattr(service, "name", "?"), exc)
        return None


def _simulate_stats(service):  # UNUSED
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


@shared_task(soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
@skip_if_recent("coalesce:collect-metrics", TASK_TIME_LIMIT_STANDARD[0])
def collect_metrics_task() -> None:
    """
    Collect metrics for all active services with running deployments.
    Pre-fetches active deployments in a single query and bounds collection
    with a deadline so old metric pruning always runs inside the task limit.
    """
    now = timezone.now()
    deadline = time.monotonic() + max(30, TASK_TIME_LIMIT_STANDARD[0] - 20)
    services = list(
        Service.objects.select_related("server").only(
            "id", "cpu_cores", "memory_mb", "owner_id", "name",
            "alert_config", "active_runtime_id",
            "server__id", "server__is_lite_agent",
        )
    )
    active_by_service: dict = {}
    for dep in (
        Deployment.objects.filter(
            service_id__in=[s.id for s in services],
            status=Deployment.Status.ACTIVE,
        )
        .only("id", "service_id", "container_id", "verified_runtime_id", "created_at")
        .order_by("service_id", "-created_at")
    ):
        if dep.service_id not in active_by_service:
            active_by_service[dep.service_id] = dep

    replicas_by_service: dict = {}
    try:
        from apps.autoscaler.models.replica import ServiceReplica

        for rep in (
            ServiceReplica.objects.filter(
                service_id__in=[s.id for s in services],
                status='RUNNING',
            )
            .select_related('node')
            .only('id', 'service_id', 'container_name', 'container_id', 'node__id', 'node__host', 'node__wg_address', 'node__api_token', 'node__is_lite_agent')
        ):
            replicas_by_service.setdefault(rep.service_id, []).append(rep)
    except Exception:
        pass

    collected = 0
    for service in services:
        if time.monotonic() >= deadline:
            logger.warning("collect_metrics_task: time budget reached after %d services; deferring remainder", collected)
            break
        latest = active_by_service.get(service.id)
        container_id = getattr(latest, 'container_id', None) if latest else None

        primary_stats = _collect_remote_container_stats(service, latest) if latest else None
        if primary_stats is None:
            primary_stats = _collect_container_stats(container_id, service.cpu_cores)

        samples: list[dict] = [primary_stats] if primary_stats is not None else []
        for rep in replicas_by_service.get(service.id, []):
            ref = (rep.container_name or rep.container_id or '').strip()
            if not ref:
                continue
            r_stats = None
            if rep.node_id:
                try:
                    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
                    data = RemoteOrchestrator(rep.node).get_container_stats(ref)
                    if data and data.get('cpu_usage') is not None:
                        r_stats = {
                            'cpu_usage': float(data.get('cpu_usage') or 0),
                            'cpu_limit': float(service.cpu_cores or data.get('cpu_limit') or 1),
                            'memory_usage': int(data.get('memory_usage') or 0),
                            'memory_limit': int(data.get('memory_limit') or service.memory_mb or 512),
                            'network_rx_bytes': int(data.get('network_rx_bytes') or 0),
                            'network_tx_bytes': int(data.get('network_tx_bytes') or 0),
                            'disk_read_bytes': int(data.get('disk_read_bytes') or 0),
                            'disk_write_bytes': int(data.get('disk_write_bytes') or 0),
                        }
                except Exception:
                    r_stats = None
            else:
                r_stats = _collect_container_stats(ref, service.cpu_cores)
            if r_stats is not None:
                samples.append(r_stats)

        if not samples:
            continue

        n = len(samples)
        stats = {
            'cpu_usage': round(sum(s['cpu_usage'] for s in samples) / n, 4),
            'cpu_limit': samples[0]['cpu_limit'],
            'memory_usage': int(sum(s['memory_usage'] for s in samples) / n),
            'memory_limit': samples[0]['memory_limit'],
            'network_rx_bytes': sum(s['network_rx_bytes'] for s in samples),
            'network_tx_bytes': sum(s['network_tx_bytes'] for s in samples),
            'disk_read_bytes': sum(s['disk_read_bytes'] for s in samples),
            'disk_write_bytes': sum(s['disk_write_bytes'] for s in samples),
        }

        ServiceMetric.objects.create(
            service=service,
            timestamp=now,
            **stats,
        )
        collected += 1

        # Check resource thresholds and fire alerts (include host disk usage without
        # mutating stats passed into ServiceMetric.objects.create).
        alert_stats = dict(stats)
        if 'disk_percent' not in alert_stats:
            try:
                import shutil as _shutil
                _disk_path = '/var/lib/docker' if os.path.exists('/var/lib/docker') else '/'
                _du = _shutil.disk_usage(_disk_path)
                if _du.total > 0:
                    alert_stats['disk_percent'] = round((_du.used / _du.total) * 100.0, 2)
            except OSError:
                pass
        _check_metric_thresholds(service, alert_stats, now)

    cutoff = now - timezone.timedelta(days=7)
    deleted, _ = ServiceMetric.objects.filter(timestamp__lt=cutoff).delete()
    if deleted:
        logger.info("Pruned %d old metric records", deleted)

    logger.info("Collected metrics for %d services", collected)


@shared_task(soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
def cleanup_build_cache_task(prune_all=False) -> dict:
    """Clean up Docker build cache to free disk space.

    Daily beat passes prune only cache older than BUILD_CACHE_MAX_AGE_HOURS
    (default 24h). An explicit operator click passes ``prune_all=True`` and
    drops everything unused — the confirm dialog already warns next builds
    take longer. Returns the reclaimed megabytes (0 when Docker is down).
    """
    # Builder prune scans 100+ cache records on this daemon (took >5s and
    # timed out at the default — the task then reported success with 0 MB).
    # Dedicated long-timeout client; fits inside the STANDARD time limits.
    client = _get_docker_client(timeout=120)
    if not client:
        logger.info("Docker unavailable, skipping build cache cleanup")
        return {"reclaimed_mb": 0}

    try:
        max_age_hours = max(1, int(os.environ.get(
            "BUILD_CACHE_MAX_AGE_HOURS", BUILD_CACHE_MAX_AGE_HOURS)))
    except (TypeError, ValueError):
        max_age_hours = BUILD_CACHE_MAX_AGE_HOURS
    try:
        if prune_all:
            # all=True: drop ALL unused cache, not just dangling records.
            # Without it the daemon reports 0 reclaimed while df shows GBs
            # reclaimable (2026-09-23: 15GB, reclaimed 0).
            result = client.api.prune_builds(all=True)
        else:
            result = client.api.prune_builds(filters={'until': f'{max_age_hours}h'})
        reclaimed = (result.get('SpaceReclaimed') or 0) // (1024 * 1024)
        logger.info("Build cache cleanup (all=%s): reclaimed %d MB",
                    prune_all, reclaimed)
        return {"reclaimed_mb": reclaimed}
    except Exception as e:
        logger.warning("Build cache cleanup failed: %s", e)
        return {"reclaimed_mb": 0}


def _check_metric_thresholds(service, stats, now):
    """Fire resource alerts and persist ResourceAlert rows when metrics breach thresholds."""
    try:
        alert_cfg = getattr(service, 'alert_config', None) or {}
        cpu_crit = float(alert_cfg.get('cpu_critical') or 90.0)
        cpu_warn = float(alert_cfg.get('cpu_warning') or cpu_crit)
        mem_crit = float(alert_cfg.get('memory_critical') or 90.0)
        mem_warn = float(alert_cfg.get('memory_warning') or mem_crit)
        disk_crit = float(alert_cfg.get('disk_critical') or 90.0)
        disk_warn = float(alert_cfg.get('disk_warning') or disk_crit)

        cpu_limit = float(stats.get('cpu_limit') or 1.0)
        cpu_usage = float(stats.get('cpu_usage') or 0.0)
        cpu_pct = (cpu_usage / cpu_limit * 100.0) if cpu_limit > 0 else 0.0

        mem_mb = float(stats.get('memory_usage') or 0.0)
        mem_limit_mb = float(stats.get('memory_limit') or getattr(service, 'memory_mb', 0) or 0.0)
        mem_pct = (mem_mb / mem_limit_mb * 100.0) if mem_limit_mb > 0 else (100.0 if mem_mb > 2000 else 0.0)

        disk_pct = float(stats.get('disk_percent') or 0.0)

        breaches = []  # list of (metric, current_pct, threshold, severity, text)
        if cpu_pct > cpu_crit:
            breaches.append(('cpu', cpu_pct, cpu_crit, 'CRITICAL', f"CPU at {cpu_pct:.0f}%"))
        elif cpu_pct > cpu_warn:
            breaches.append(('cpu', cpu_pct, cpu_warn, 'WARNING', f"CPU at {cpu_pct:.0f}%"))

        if mem_limit_mb > 0:
            if mem_pct > mem_crit:
                breaches.append(('memory', mem_pct, mem_crit, 'CRITICAL', f"Memory at {mem_mb:.0f}MB ({mem_pct:.0f}%)"))
            elif mem_pct > mem_warn:
                breaches.append(('memory', mem_pct, mem_warn, 'WARNING', f"Memory at {mem_mb:.0f}MB ({mem_pct:.0f}%)"))
        elif mem_mb > 2000:
            breaches.append(('memory', 100.0, 90.0, 'WARNING', f"Memory at {mem_mb:.0f}MB"))

        if disk_pct > disk_crit:
            breaches.append(('disk', disk_pct, disk_crit, 'CRITICAL', f"Disk at {disk_pct:.0f}%"))
        elif disk_pct > disk_warn:
            breaches.append(('disk', disk_pct, disk_warn, 'WARNING', f"Disk at {disk_pct:.0f}%"))

        if breaches:
            from django.core.cache import cache
            from apps.notifications.models import ResourceAlert
            from apps.notifications.tasks import notify_health_alert

            hour_bucket = now.strftime('%Y%m%d%H')
            new_breaches = []
            for metric_name, cur_val, thresh_val, sev, msg_text in breaches:
                b_key = f"metrics_alert:{service.id}:{metric_name}:{sev}:{hour_bucket}"
                if not cache.get(b_key):
                    cache.set(b_key, 1, 3600)  # 1 hour dedup per metric & severity
                    new_breaches.append((metric_name, cur_val, thresh_val, sev, msg_text))

            if not new_breaches:
                return

            for metric_name, cur_val, thresh_val, sev, msg_text in new_breaches:
                try:
                    ResourceAlert.objects.create(
                        service=service,
                        severity=sev,
                        metric=metric_name,
                        threshold=round(thresh_val, 1),
                        current_value=round(cur_val, 1),
                        message=msg_text,
                    )
                except Exception as db_exc:
                    logger.debug("Failed to persist ResourceAlert for %s: %s", service.name, db_exc)

            top = max(new_breaches, key=lambda b: (1 if b[3] == 'CRITICAL' else 0, b[1]))
            overall_severity = 'CRITICAL' if any(b[3] == 'CRITICAL' for b in new_breaches) else 'WARNING'

            notify_health_alert.delay(
                service.owner_id,
                service.name,
                'resource',
                current_value=round(top[1], 1),
                threshold=round(top[2], 1),
                severity=overall_severity,
                message='; '.join(b[4] for b in new_breaches),
            )
    except Exception as exc:
        logger.debug("Failed to check metric thresholds: %s", exc)
