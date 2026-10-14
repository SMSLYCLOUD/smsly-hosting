from __future__ import annotations

import logging

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM

logger = logging.getLogger(__name__)


@shared_task(
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
    name="apps.deployments.tasks.deploy.caddy.sync_caddy_task",
)
def sync_caddy_task():
    """Regenerate and apply Caddyfile, then push to all full nodes.

    Dispatched asynchronously from domain add/delete actions to avoid
    blocking the HTTP response (the full cycle can take 30+ seconds).
    """
    try:
        from apps.deployments.services.caddy_manager.utils import caddy_disabled_mode, caddy_node_mode
        from apps.deployments.models import PlatformConfig

        if caddy_disabled_mode():
            logger.debug("sync_caddy_task: Caddy-disabled mode, skipping")
            return {"ok": True, "message": "Skipped (Caddy-disabled)"}

        config = PlatformConfig.load()

        if caddy_node_mode():
            _regenerate_node_caddyfile(config)
            return {"ok": True, "message": "Node Caddyfile regenerated"}

        from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            logger.info("sync_caddy_task: Caddyfile regenerated")
        else:
            logger.warning("sync_caddy_task: regeneration failed: %s", result.get('message'))

        _regenerate_node_caddyfile(config)

        return {"ok": bool(result.get("ok")), "message": str(result.get("message", ""))}
    except Exception as exc:
        logger.warning("sync_caddy_task failed: %s", exc)
        return {"ok": False, "message": str(exc)}


def _regenerate_caddyfile(deployment=None):
    try:
        from apps.deployments.services.caddy_manager.utils import caddy_disabled_mode
        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()
        from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

        if caddy_disabled_mode():
            logger.debug("Caddy-disabled mode: skipping _regenerate_caddyfile()")
            return

        from apps.deployments.services.caddy_manager.utils import caddy_node_mode
        if caddy_node_mode():
            _regenerate_node_caddyfile(config)
        else:
            content = generate_caddyfile(config)
            cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
            result = apply_caddyfile(content, cloudflare_token=cf_token)
            if result.get('ok'):
                logger.info("Caddyfile regenerated after deployment")
            else:
                msg = result.get('message')
                logger.error("Caddyfile regeneration failed: %s", msg)
                if deployment is not None:
                    try:
                        from apps.deployments.utils import append_log
                        append_log(deployment, f"[CADDY-WARN] Caddyfile regeneration failed: {msg}\n")
                    except Exception:
                        pass
    except Exception as exc:
        logger.error("Could not regenerate Caddyfile: %s", exc)
        if deployment is not None:
            try:
                from apps.deployments.utils import append_log
                append_log(deployment, f"[CADDY-WARN] Could not regenerate Caddyfile: {exc}\n")
            except Exception:
                pass


def _regenerate_node_caddyfile(config=None):
    """Regenerate and push the Caddyfile to all full nodes via SSH."""
    try:
        from apps.deployments.models.core import ManagedServer

        nodes = ManagedServer.objects.filter(is_primary=False, is_lite_agent=False)
        if not nodes.exists():
            logger.debug("No full nodes found; skipping node Caddyfile regeneration")
            return

        for node in nodes:
            result = push_caddy_to_node(str(node.id))
            if result.get('ok'):
                logger.info("Node Caddyfile regenerated for %s", node.name)
            else:
                logger.warning(
                    "Node Caddyfile regeneration failed for %s: %s",
                    node.name, result.get('message'),
                )
    except Exception as exc:
        logger.warning("Could not regenerate node Caddyfile: %s", exc)


def push_caddy_to_node(server_id: str) -> dict:
    """Generate the node-specific Caddyfile and push it to a remote full node via SSH.

    Used after toggle changes on master to update the node's own Caddy.
    """
    from apps.deployments.models.core import ManagedServer
    from apps.deployments.services.ssh_client import SSHClient

    try:
        server = ManagedServer.objects.get(id=server_id)
    except ManagedServer.DoesNotExist:
        logger.warning("push_caddy_to_node: server %s not found", server_id)
        return {"ok": False, "message": "Server not found"}

    if getattr(server, 'is_lite_agent', False):
        logger.info("push_caddy_to_node: skipping lite agent %s", server_id)
        return {"ok": True, "message": "Lite agent — no local Caddy"}

    if getattr(server, 'is_primary', False):
        logger.info("push_caddy_to_node: skipping primary server %s", server_id)
        return {"ok": True, "message": "Primary server — uses master Caddy"}

    if not server.ssh_key and not server.ssh_password:
        logger.warning("push_caddy_to_node: no SSH credentials for %s", server.name)
        return {"ok": False, "message": "No SSH credentials for node"}

    try:
        from apps.deployments.services.caddy_manager.config_generation import generate_node_caddyfile
        content = generate_node_caddyfile(server)
    except Exception as exc:
        logger.warning("push_caddy_to_node: failed to generate Caddyfile: %s", exc)
        return {"ok": False, "message": f"Generate failed: {exc}"}

    if not content:
        logger.info("push_caddy_to_node: empty Caddyfile for %s — skipping", server_id)
        return {"ok": True, "message": "Empty Caddyfile — nothing to push"}

    ssh = SSHClient(
        ip=server.host,
        key_content=server.ssh_key,
        key_passphrase=server.ssh_key_passphrase,
        password=server.ssh_password,
        user=server.ssh_user,
        port=server.ssh_port,
        wg_address=server.wg_address,
    )
    try:
        ssh.connect()

        caddy_path = "/opt/smsly-hosting/infrastructure/docker/Caddyfile.node"

        import os
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="Caddyfile", delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            ssh.upload_file(tmp_path, caddy_path)
        finally:
            os.unlink(tmp_path)

        ssh.exec_command(f"chmod 644 {caddy_path}", timeout=10)

        reload_out, reload_err, reload_code = ssh.exec_command(
            "cd /opt/smsly-hosting && docker compose -f infrastructure/docker/docker-compose.node.yml exec -T caddy caddy reload --config /etc/caddy/Caddyfile --force",
            timeout=30,
            raise_on_error=False,
        )
        if reload_code != 0:
            logger.warning(
                "push_caddy_to_node: caddy reload returned %s: %s",
                reload_code, (reload_out or "") + (reload_err or ""),
            )
            return {"ok": False, "message": f"Caddy reload failed (exit {reload_code})"}

        logger.info("Caddyfile pushed to node %s and Caddy reloaded", server.name)
        # Convergence bookkeeping: stamp the pushed content hash so the
        # reconcile beat can detect drift (e.g. pushes lost to an SSH
        # outage) without re-pushing identical configs. Best-effort —
        # never fails the push itself.
        try:
            import hashlib as _hashlib
            _meta = dict(getattr(server, "provider_metadata", None) or {})
            _meta["caddy_config_sha256"] = _hashlib.sha256(content.encode()).hexdigest()
            server.provider_metadata = _meta
            server.save(update_fields=["provider_metadata"])
        except Exception:
            pass
        return {"ok": True, "message": "Caddyfile pushed, written, and Caddy reloaded"}
    except Exception as exc:
        logger.warning("push_caddy_to_node: failed to push to node: %s", exc)
        return {"ok": False, "message": f"Push failed: {exc}"}
    finally:
        ssh.close()


def _node_live_caddy_matches(node, want_hash: str) -> bool:
    """True when the node's live Caddyfile hashes to ``want_hash``.

    Fail-open: any SSH/read error returns True (trust the stamp) so a
    monitoring hiccup can never trigger a push storm. Never raises.
    """
    try:
        from apps.deployments.services.ssh_client import SSHClient
        ssh = SSHClient(
            ip=node.host,
            key_content=node.ssh_key,
            key_passphrase=node.ssh_key_passphrase,
            password=node.ssh_password,
            user=node.ssh_user,
            port=node.ssh_port,
            wg_address=node.wg_address,
        )
        ssh.connect()
        try:
            out, _, _ = ssh.exec_command(
                "sha256sum /opt/smsly-hosting/infrastructure/docker/Caddyfile.node "
                "2>/dev/null | awk '{print $1}'",
                timeout=30,
                raise_on_error=False,
            )
        finally:
            ssh.close()
        live = (out or "").strip().split()[0] if (out or "").strip() else ""
        return bool(live) and live == want_hash
    except Exception as exc:
        logger.debug("reconcile_node_caddyfiles: live check failed: %s", exc)
        return True


@shared_task(
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
    name="apps.deployments.tasks.deploy.caddy.reconcile_node_caddyfiles_task",
)
def reconcile_node_caddyfiles_task() -> dict:
    """Converge full-node Caddyfiles by content hash (every 15m).

    The SSH fanout in push_caddy_to_node is fire-and-forget: an SSH
    outage leaves node edges serving stale configs with only a warning
    in the logs. This beat regenerates each node's content, compares
    against the last-pushed hash stamped on the server row, and pushes
    on drift (or when never stamped). OFFLINE nodes are skipped (a push
    cannot succeed); empty content is never pushed. Never raises —
    per-node failures are reported, not fatal.

    Single-writer rule: the MASTER owns fanout. Node-local beats must
    NOT run this task — a node regenerating from its own (possibly
    older) code would clobber the master's push and the stamp would
    keep matching, hiding the drift forever (2026-10-13: node served
    a pre-WAF-exemption Caddyfile while master reported converged).

    The stamp alone is not trusted either: when it matches, the live
    node file hash is read back over SSH and a mismatch still pushes.
    """
    import os as _os

    from apps.deployments.models.core import ManagedServer
    report: dict = {"pushed": [], "converged": [], "skipped": [], "failed": []}
    try:
        if str(_os.environ.get("INSTALL_MODE", "") or "").strip().lower() == "node":
            report["skipped"].append("non-master host (master owns fanout)")
            return {"status": "ok", **report}
        # Same single-writer rule via the established node-identity vars
        # (mirror of orphan_addon_gc): full-stack nodes run the same image
        # as master, and INSTALL_MODE is unset on older installs
        # (2026-10-14: node beat kept clobbering the WAF-sync exemption
        # because its env lacked INSTALL_MODE). Either var proves node-hood.
        _node_id = (_os.environ.get("SMSLY_NODE_ID", "") or "").strip()
        _node_queue = (_os.environ.get("SMSLY_NODE_QUEUE", "") or "").strip()
        if _node_id or _node_queue:
            report["skipped"].append("node worker (master owns fanout)")
            return {"status": "ok", **report}
        nodes = ManagedServer.objects.filter(is_primary=False, is_lite_agent=False)
    except Exception as exc:
        logger.warning("reconcile_node_caddyfiles: node query failed: %s", exc)
        return {"status": "error", "error": str(exc)[:200], **report}
    for node in nodes:
        name = getattr(node, "name", str(getattr(node, "id", "?")))
        try:
            if str(getattr(node, "status", "") or "").upper() == "OFFLINE":
                report["skipped"].append(f"{name} (offline)")
                continue
            from apps.deployments.services.caddy_manager.config_generation import (
                generate_node_caddyfile,
            )
            content = generate_node_caddyfile(node)
            if not content:
                report["skipped"].append(f"{name} (empty)")
                continue
            import hashlib as _hashlib
            want = _hashlib.sha256(content.encode()).hexdigest()
            try:
                have = dict(getattr(node, "provider_metadata", None) or {}).get(
                    "caddy_config_sha256", "")
            except Exception:
                have = ""
            if have and have == want:
                if _node_live_caddy_matches(node, want):
                    report["converged"].append(name)
                    continue
                logger.warning(
                    "reconcile_node_caddyfiles: %s stamp matches but live "
                    "content differs — re-pushing", name,
                )
            result = push_caddy_to_node(str(node.id))
            if result.get("ok"):
                report["pushed"].append(name)
            else:
                report["failed"].append(f"{name} ({result.get('message', '')[:120]})")
        except Exception as exc:
            report["failed"].append(f"{name} ({exc.__class__.__name__})")
    return {"status": "ok", **report}
