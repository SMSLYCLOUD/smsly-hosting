from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.deployments.tasks.deploy.caddy.sync_caddy_task")
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


def _regenerate_caddyfile():
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
                logger.warning("Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:
        logger.warning("Could not regenerate Caddyfile: %s", exc)


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
            "cd /opt/smsly-hosting && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --force",
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
        return {"ok": True, "message": "Caddyfile pushed, written, and Caddy reloaded"}
    except Exception as exc:
        logger.warning("push_caddy_to_node: failed to push to node: %s", exc)
        return {"ok": False, "message": f"Push failed: {exc}"}
    finally:
        ssh.close()
