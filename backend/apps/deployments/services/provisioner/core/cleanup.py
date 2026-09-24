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

    Handles: DB user, iptables rules, WireGuard peer, DNS record, sensitive fields.
    """
    metadata = server.provider_metadata or {}

    # 1. Drop the DB user ONLY if an unfinished run created it
    # (node_db_user_pending marker). A dead re-provision of a live node
    # leaves no marker — the role preexisted — so the live role is
    # preserved. Dropping unconditionally here once killed healthy nodes
    # whose re-provision worker died mid-run.
    node_db_user = metadata.get("node_db_user")
    if node_db_user and metadata.get("node_db_user_pending"):
        _drop_db_user(node_db_user)
        try:
            from apps.deployments.services.provisioner.helpers.database import (
                _rerender_pgcat_config,
            )
            _rerender_pgcat_config()
        except Exception as exc:
            logger.debug("Rollback: pgcat re-render skipped for %s: %s", server.name, exc)
    elif node_db_user:
        _append_log(
            server,
            f"🧹 Keeping pre-existing DB user (no unfinished creation): {node_db_user}",
        )

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
            if getattr(server, "is_lite_agent", False):
                subprocess.run(
                    ["ufw", "delete", "allow", "from", validated_ip,
                     "to", "any", "port", "5432", "proto", "tcp"],
                    capture_output=True, timeout=5,
                )
        except (ValueError, Exception):
            pass

    # 2b. Remove the mesh-IP registry rule (added untracked by firewall harden)
    _wg = getattr(server, "wg_address", None) or ""
    if _wg:
        try:
            import ipaddress as _ipa
            _validated_wg = str(_ipa.ip_address(str(_wg)))
            subprocess.run(
                ["iptables", "-D", "DOCKER-USER",
                 "-s", _validated_wg, "-p", "tcp", "--dport", "5000",
                 "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )
        except Exception:
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

    # 4. Remove DNS record if node_domain was assigned (30s guard: a
    # Cloudflare hang must not stall the sweeper past its time limit)
    node_domain = getattr(server, "node_domain", "") or ""
    if node_domain:
        try:
            from apps.deployments.models.core import PlatformConfig
            from apps.domains.services.dns import delete_dns_record
            config = PlatformConfig.load()
            cf_token = getattr(config, "cloudflare_api_token", "") or ""
            if cf_token:
                import threading as _threading
                _dns_exc: list[Exception] = []

                def _do_delete():
                    try:
                        delete_dns_record(node_domain, cf_token)
                    except Exception as exc:  # noqa: BLE001
                        _dns_exc.append(exc)

                _t = _threading.Thread(target=_do_delete, daemon=True)
                _t.start()
                _t.join(timeout=30)
                if _t.is_alive():
                    logger.warning("Rollback: DNS cleanup timed out for %s", node_domain)
                elif _dns_exc:
                    raise _dns_exc[0]
                else:
                    logger.info("Rollback: deleted DNS record for %s", node_domain)
        except Exception as exc:
            logger.debug("Rollback: DNS cleanup failed for %s: %s", server.name, exc)

    # 5. Clear sensitive fields — restoring the operator's SSH key backup
    # instead of blanking it (blanking strands key-only hosts). The
    # dropped node_db_user marker is cleared too since the role is gone.
    update_fields = []
    _meta = dict(getattr(server, "provider_metadata", None) or {})
    _backup = _meta.get("ssh_key_backup")
    _meta_changed = False
    if _backup:
        server.ssh_key = _backup
        update_fields.append("ssh_key")
        try:
            del _meta["ssh_key_backup"]
            _meta_changed = True
        except Exception:
            pass
    if _meta.pop("node_db_user", None) is not None:
        _meta_changed = True
    if _meta.pop("node_db_user_pending", None) is not None:
        _meta_changed = True
    if _meta_changed:
        try:
            server.provider_metadata = _meta
            update_fields.append("provider_metadata")
        except Exception:
            pass
    # NOTE: node_db_password / gateway_secret are deliberately NOT blanked
    # here. The sweeper cannot know whether the record matches the live
    # node (dead re-provision) or a dead first run (retry reuses the
    # stored password to recreate the role). Blanking destroys forensics
    # and breaks retry reuse; same-run rollback (which has a snapshot)
    # remains the only path that clears these fields.
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
    # ── 1. Mark stale PROVISIONING servers as FAILED ──
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

    # ── 2. Purge PENDING servers that never connected within 12 hours ──
    #    These are self-provisioned nodes where the user ran the bootstrap
    #    script but the node never called back (agent-registrar heartbeat).
    #    Full rollback: VPN peer, DNS record, iptables, DB user, then delete.
    pending_cutoff = timezone.now() - timedelta(hours=12)
    stale_pending = ManagedServer.objects.filter(
        provision_status=ManagedServer.ProvisionStatus.PENDING,
        updated_at__lt=pending_cutoff,
    )
    for server in stale_pending:
        _append_log(
            server,
            (
                "Purging server record — no connection heard within 12 hours of "
                "provisioning. Rolling back VPN peer, DNS, and other resources."
            ),
        )
        _rollback_stale_provisioning(server)
        server_name = server.name
        server_id = str(server.id)
        server.delete()
        cleaned += 1
        logger.warning(
            "Purged stale PENDING server %s (%s) — no connection within 12h",
            server_name, server_id,
        )

    if cleaned:
        logger.warning("Auto-cleaned %d stale provisioning records", cleaned)
    return cleaned
