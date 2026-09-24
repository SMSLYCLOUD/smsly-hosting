"""Celery task: keep the CoreDNS mesh zone in sync with mesh peers.

Regenerates /coredns-config/{Corefile,mesh.hosts} from the
MeshNetwork/WireGuardPeer rows. The CoreDNS `hosts` plugin re-reads
the zone file automatically when it changes — no container restart.

Reconcile triggers:
  - beat schedule (mesh-dns-sync-every-5min) — catches drift
  - add_peer_to_mesh / remove_peer_from_mesh enqueue this task
  - backend startup (schedule_startup_caddy_sync) — first boot seeding
"""

import logging

from celery import shared_task
from django.core.cache import cache

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.deployments.tasks.infra.tasks_mesh_dns.sync_mesh_dns_task",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def sync_mesh_dns_task():
    """Regenerate the CoreDNS mesh zone from the mesh peer rows."""
    if not cache.add("mesh-dns-sync-lock", "1", timeout=120):
        logger.debug("Mesh DNS sync skipped — another sync is running")
        return {"ok": True, "skipped": True, "message": "sync already running"}
    try:
        from apps.deployments.services.mesh_dns import apply_mesh_dns

        result = apply_mesh_dns()
        if result.get("ok"):
            logger.info("Mesh DNS sync: %s", result.get("message"))
        else:
            logger.warning("Mesh DNS sync failed: %s", result.get("message"))
        return result
    except Exception as exc:  # noqa: BLE001 — task must never crash the worker
        logger.error("Mesh DNS sync raised: %s", exc)
        return {"ok": False, "message": str(exc)}
    finally:
        cache.delete("mesh-dns-sync-lock")


def queue_mesh_dns_sync() -> None:
    """Best-effort enqueue of the zone sync (never raises)."""
    try:
        sync_mesh_dns_task.delay()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not queue mesh DNS sync: %s", exc)
