import logging
import subprocess
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK
from apps.deployments.models.servers import ManagedServer

from ..helpers import PROVISION_TIMEOUT_SECONDS, _append_log

logger = logging.getLogger(__name__)


def _rollback_stale_provisioning(server: ManagedServer) -> None:
    """Best-effort cleanup of resources created during a stale provisioning attempt.

    This is a simplified rollback — it handles the common cases (DB user,
    SSH key, iptables rules, WireGuard peer) using only information stored
    on the server record.  It does NOT attempt DNS cleanup or remote SSH
    key removal because those require connectivity that may not exist.
    """
    metadata = server.provider_metadata or {}

    # 1. Drop the DB user if one was created
    node_db_user = metadata.get("node_db_user")
    if node_db_user:
        _drop_db_user(node_db_user)

    # 2. Remove iptables rules for the node's public IP
    host = getattr(server, "host", "") or ""
    if host:
        try:
            import ipaddress
            validated_ip = str(ipaddress.ip_address(host))
            subprocess.run(
                ["iptables", "-D", "DOCKER-USER",
                 "-s", validated_ip, "-p", "tcp", "--dport", "5000",
                 "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )
        except (ValueError, Exception):
            pass

    # 3. Remove WireGuard peer if one was created
    wg_address = getattr(server, "wg_address", None)
    if wg_address:
        try:
            from apps.deployments.models.mesh import WireGuardPeer
            from apps.deployments.services.wireguard_service import WireGuardService
            peer = WireGuardPeer.objects.filter(
                server=server, is_local=False, is_active=True,
            ).first()
            if peer:
                WireGuardService.remove_peer_from_mesh(peer)
        except Exception as exc:
            logger.debug("Rollback: WG peer removal failed for %s: %s", server.name, exc)

    # 4. Clear sensitive fields
    update_fields = []
    if server.ssh_key:
        server.ssh_key = ""
        update_fields.append("ssh_key")
    if getattr(server, "node_db_password", None):
        server.node_db_password = ""
        update_fields.append("node_db_password")
    if server.gateway_secret:
        server.gateway_secret = ""
        update_fields.append("gateway_secret")
    if update_fields:
        update_fields.append("updated_at")
        try:
            server.save(update_fields=update_fields)
        except Exception as exc:
            logger.warning("Rollback: failed to clear sensitive fields for %s: %s", server.name, exc)


def _drop_db_user(username: str) -> None:
    import os
    master_db_url = os.environ.get("DATABASE_URL")
    if not master_db_url:
        return
    try:
        import psycopg2
        from psycopg2 import sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        conn = psycopg2.connect(master_db_url, connect_timeout=10)
        try:
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(username)))
                cur.execute(sql.SQL("DROP USER IF EXISTS {}").format(sql.Identifier(username)))
        finally:
            conn.close()
        logger.info("Rollback: dropped DB user %s", username)
    except Exception as exc:
        logger.warning("Rollback: failed to drop DB user %s: %s", username, exc)


@shared_task(name="apps.deployments.services.provisioner.cleanup_stale_server_provisioning", soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1])
def cleanup_stale_server_provisioning():
    stale_after_seconds = max(3600, PROVISION_TIMEOUT_SECONDS * 2)
    cutoff = timezone.now() - timedelta(seconds=stale_after_seconds)
    stale_servers = ManagedServer.objects.filter(
        provision_status=ManagedServer.ProvisionStatus.PROVISIONING,
        updated_at__lt=cutoff,
    )

    cleaned = 0
    for server in stale_servers:
        _rollback_stale_provisioning(server)
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            (
                "Provisioning was auto-marked as failed because no updates were "
                f"received for over {stale_after_seconds} seconds."
            ),
        )
        cleaned += 1

    pending_cutoff = timezone.now() - timedelta(hours=24)
    stale_pending = ManagedServer.objects.filter(
        provision_status=ManagedServer.ProvisionStatus.PENDING,
        updated_at__lt=pending_cutoff,
    )
    for server in stale_pending:
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            (
                "Provisioning was auto-marked as failed because the server was "
                "never provisioned (stuck in PENDING for over 24 hours)."
            ),
        )
        cleaned += 1

    if cleaned:
        logger.warning("Auto-cleaned %d stale provisioning records", cleaned)
    return cleaned
