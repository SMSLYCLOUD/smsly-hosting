"""
Redis failover recovery service.

Detects when ``redis-primary`` has been orphaned by a Sentinel failover
and cleans it up so the Docker Compose service can be recreated as a replica.
"""

import logging

import docker
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)

PRIMARY_CONTAINER = "smsly-redis-primary"


def get_sentinel_master():
    """Return (host, port) of the current Sentinel-managed master, or None."""
    import os
    hosts = os.environ.get("SENTINEL_HOSTS", "").strip()
    if not hosts:
        return None
    try:
        from config.redis_sentinel import get_sentinel
        sentinel = get_sentinel()
        if sentinel is None:
            return None
        return sentinel.discover_master("mymaster")
    except Exception:
        logger.exception("Failed to query Sentinel for current master")
        return None


def _resolve_host(host: str) -> str | None:
    """Resolve a hostname to an IP, or None when unresolvable."""
    import socket
    try:
        return socket.gethostbyname((host or "").strip())
    except Exception:
        return None


def _container_addresses(container) -> set[str]:
    """All IPs, Docker network aliases, and the name of a container."""
    addrs: set[str] = set()
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {}) or {}
    for net_config in networks.values():
        net_config = net_config or {}
        ip = net_config.get("IPAddress")
        if ip:
            addrs.add(str(ip).strip())
        for alias in net_config.get("Aliases") or []:
            if alias:
                addrs.add(str(alias).strip().lower())
    name = (getattr(container, "name", "") or "").strip().lower()
    if name:
        addrs.add(name)
    return addrs


def _container_is_master(container, master_host: str) -> bool:
    """True when the container IS the Sentinel-reported master.

    Compares bidirectionally (resolved IPs AND names/aliases) because
    Sentinel may report a hostname (redis-primary) while Docker reports
    an IP (172.18.0.x), or vice versa. A one-sided string comparison
    can never match and caused false "orphan" deletions (2026-09-04:
    a healthy primary was deleted because "172.18.0.13" != "redis-primary").
    """
    if not master_host:
        return False
    addrs = _container_addresses(container)
    if master_host.strip().lower() in addrs:
        return True
    master_ip = _resolve_host(master_host)
    return bool(master_ip and master_ip in addrs)


def check_and_recover(dry_run: bool = False) -> dict:
    """
    Check if a Redis failover has orphaned the ``redis-primary`` container.

    Returns a dict with:
      - status: "ok" (no action needed), "recovered" (action taken),
        "not_found" (no container), "error", or "skipped" (no Sentinel)
      - message: human-readable description
    """
    result = {"status": "ok", "message": "", "container_stopped": False}

    master = get_sentinel_master()
    if master is None:
        result["status"] = "skipped"
        result["message"] = "Sentinel not configured (SENTINEL_HOSTS not set)."
        return result

    master_host, master_port = master
    logger.info("Sentinel master: %s:%s", master_host, master_port)

    try:
        client = docker.from_env()
    except Exception as exc:
        result["status"] = "error"
        result["message"] = f"Docker client error: {exc}"
        return result

    try:
        container = client.containers.get(PRIMARY_CONTAINER)
    except docker.errors.NotFound:
        result["status"] = "not_found"
        result["message"] = f"Container '{PRIMARY_CONTAINER}' not found."
        return result

    container_ip = next(
        (a for a in _container_addresses(container) if a and a[0].isdigit()),
        None,
    )
    logger.info(
        "Container '%s' status=%s ip=%s",
        PRIMARY_CONTAINER, container.status, container_ip,
    )

    if _container_is_master(container, master_host):
        result["message"] = (
            f"Container '{PRIMARY_CONTAINER}' ({container_ip}) IS the Sentinel master."
        )
        return result

    # Fail-safe: only delete when the master positively resolves to a
    # DIFFERENT address. If the master hostname doesn't resolve (DNS down,
    # sentinel mid-failover) we know nothing — deleting then risks killing
    # the healthy primary, which is exactly what happened on 2026-09-04.
    master_ip = _resolve_host(master_host)
    if not master_ip:
        result["status"] = "skipped"
        result["message"] = (
            f"Sentinel master '{master_host}:{master_port}' does not resolve; "
            f"leaving container '{PRIMARY_CONTAINER}' untouched."
        )
        logger.warning(
            "Redis failover check inconclusive (master %s unresolvable) — no action taken.",
            master_host,
        )
        return result

    logger.warning(
        "Failover detected: container=%s (%s) is NOT master (%s:%s, resolved %s)",
        PRIMARY_CONTAINER, container_ip or "unknown", master_host, master_port, master_ip,
    )

    if dry_run:
        result["status"] = "dry_run"
        result["message"] = (
            f"[DRY-RUN] Would stop and remove orphaned container '{PRIMARY_CONTAINER}'."
        )
        return result

    try:
        logger.info("Stopping orphaned container '%s' ...", PRIMARY_CONTAINER)
        container.stop(timeout=10)
        logger.info("Removing orphaned container '%s' ...", PRIMARY_CONTAINER)
        container.remove(v=True)
        result["status"] = "recovered"
        result["container_stopped"] = True
        result["message"] = (
            f"Orphaned container '{PRIMARY_CONTAINER}' stopped and removed. "
            "Recreate with: docker compose up -d --no-deps redis-primary"
        )
        logger.info("Recovery complete for '%s'.", PRIMARY_CONTAINER)
    except Exception as exc:
        result["status"] = "error"
        result["message"] = f"Failed to stop/remove container: {exc}"
        logger.exception("Recovery action failed for '%s'", PRIMARY_CONTAINER)

    return result


@shared_task(
    bind=True,
    name="apps.deployments.tasks.recover_redis_failover",
    max_retries=2,
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def recover_redis_failover(self):
    """Celery beat task: detect and clean up orphaned Redis primary."""
    try:
        result = check_and_recover(dry_run=False)
        logger.info(
            "recover_redis_failover: status=%s message=%s",
            result["status"], result["message"],
        )
        return result
    except SoftTimeLimitExceeded:
        logger.error("recover_redis_failover timed out")
        return {"status": "error", "message": "Timed out"}
    except Exception as exc:
        logger.exception("recover_redis_failover failed")
        raise self.retry(exc=exc, countdown=30)
