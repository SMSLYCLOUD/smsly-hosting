from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
    """Regenerate and apply the Caddyfile for a full-node deployment."""
    try:
        from apps.deployments.services.caddy_manager import apply_caddyfile
        from apps.deployments.services.caddy_manager.config_generation import generate_node_caddyfile
        from apps.deployments.models.core import ManagedServer

        if config is None:
            from apps.deployments.models import PlatformConfig
            config = PlatformConfig.load()

        node = ManagedServer.objects.filter(is_primary=False, is_lite_agent=False).first()
        if not node:
            logger.debug("No full node found; skipping node Caddyfile regeneration")
            return

        content = generate_node_caddyfile(node)
        if not content:
            logger.debug("Empty node Caddyfile; skipping")
            return

        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            logger.info("Node Caddyfile regenerated")
        else:
            logger.warning("Node Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:
        logger.warning("Could not regenerate node Caddyfile: %s", exc)


def push_caddy_to_node(server_id: str) -> dict:
    """Generate the node-specific Caddyfile and push it to a remote full node via API.

    Used after toggle changes on master to update the node's own Caddy.
    """
    import json as json_mod
    from apps.deployments.models.core import ManagedServer, PlatformConfig

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

    try:
        from apps.deployments.services.caddy_manager.config_generation import generate_node_caddyfile
        content = generate_node_caddyfile(server)
    except Exception as exc:
        logger.warning("push_caddy_to_node: failed to generate Caddyfile: %s", exc)
        return {"ok": False, "message": f"Generate failed: {exc}"}

    if not content:
        logger.info("push_caddy_to_node: empty Caddyfile for %s — skipping", server_id)
        return {"ok": True, "message": "Empty Caddyfile — nothing to push"}

    try:
        from apps.deployments.services.remote_orchestrator.manager import RemoteOrchestrator
        client = RemoteOrchestrator(server)

        import base64
        script = (
            "import os, tempfile\n"
            f"content = {content!r}\n"
            "path = '/opt/smsly-hosting/caddy-config/Caddyfile'\n"
            "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
            "tmp = path + '.tmp'\n"
            "with open(tmp, 'w') as f:\n"
            "    f.write(content)\n"
            "os.replace(tmp, path)\n"
            "os.chmod(path, 0o664)\n"
            "print('Caddyfile written')\n"
        )

        resp = client._request(
            method='POST',
            path='/api/v1/transfers/incoming/exec/',
            json={'script': script},
            timeout=30,
        )
        if resp and resp.status_code == 200:
            logger.info("Caddyfile pushed to node %s", server.name)
            return {"ok": True, "message": "Caddyfile pushed and written"}
        else:
            status_code = resp.status_code if resp else "no response"
            body = resp.text[:300] if resp else ""
            logger.warning("push_caddy_to_node: node returned %s: %s", status_code, body)
            return {"ok": False, "message": f"Node returned {status_code}"}
    except Exception as exc:
        logger.warning("push_caddy_to_node: failed to push to node: %s", exc)
        return {"ok": False, "message": f"Push failed: {exc}"}
