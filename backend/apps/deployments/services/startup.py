"""
Startup utilities for deployments app.

We use a background thread to regenerate/apply the Caddyfile shortly after
Django boots so SSL/DNS stay in sync (Railway-style "just works").
"""

import contextlib
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_started = False


def caddy_disabled_mode() -> bool:
    """Return True when this runtime topology does not include Caddy."""
    mode = str(os.environ.get("MODE", "")).strip().lower()
    node_type = str(os.environ.get("NODE_TYPE", "")).strip().lower()
    return mode in {"agent", "agent-lite", "node"} or node_type in {
        "agent",
        "agent-lite",
        "node",
    }


def _sync_caddy_once(delay: float = 3.0):
    """Delay a few seconds to let migrations/settings load, then apply Caddy."""
    try:
        time.sleep(delay)

        # Debounce across multiple gunicorn workers using a lock file
        import os
        lock_file = "/tmp/caddy_sync_startup.lock"
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except OSError:
            # Lock exists, another worker is already doing this
            logger.debug("Startup Caddy sync skipped (lock exists)")
            return

        try:
            from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

            from apps.deployments.models import PlatformConfig

            cfg = PlatformConfig.load()
            content = generate_caddyfile(cfg)
            token = (getattr(cfg, "cloudflare_api_token", "") or "").strip()
            result = apply_caddyfile(content, cloudflare_token=token, preserve_existing_token=True)
            logger.info("Startup Caddy sync: %s", result.get("message", "ok"))
        finally:
            with contextlib.suppress(OSError):
                os.unlink(lock_file)

        # 2. Trigger Auto-Authentication for nodes missing API tokens
        try:
            from apps.deployments.tasks.infra.tasks_health import auto_authenticate_nodes_task
            auto_authenticate_nodes_task.delay()
            logger.info("Startup: Triggered auto-authentication task for nodes.")
        except Exception as e:
            logger.warning("Startup: Failed to trigger auto-auth task: %s", e)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Startup background tasks failed: %s", exc)


def _store_ssh_from_env():
    """Read NODE_SSH_PASSWORD / NODE_SSH_KEY from env and save to ManagedServer.

    Lite agents can define these in .env so SSH credentials persist across
    container restarts and the self-healing system can SSH into the node.
    Credentials are encrypted at rest via EncryptedCharField/EncryptedTextField.
    """
    ssh_password = str(os.environ.get("NODE_SSH_PASSWORD", "")).strip()
    ssh_key = str(os.environ.get("NODE_SSH_KEY", "")).strip()
    node_host = str(os.environ.get("NODE_HOST", "") or os.environ.get("HOST_IP", "")).strip()

    if ssh_key and not ssh_key.startswith("-----BEGIN "):
        logger.warning(
            "NODE_SSH_KEY is set but does not look like a valid PEM private key "
            "(must start with '-----BEGIN ...'); ignoring it."
        )
        ssh_key = ""

    if not ssh_password and not ssh_key:
        return

    try:
        from apps.deployments.models.core import ManagedServer

        server = None
        if node_host:
            server = ManagedServer.objects.filter(host=node_host).first()
        if not server:
            # Fallback: find a server with empty SSH credentials
            server = ManagedServer.objects.filter(ssh_password="", ssh_key="").first()
        if not server:
            server = ManagedServer.objects.first()

        if server:
            changed = False
            if ssh_password and server.ssh_password != ssh_password:
                server.ssh_password = ssh_password
                changed = True
            if ssh_key and server.ssh_key != ssh_key:
                server.ssh_key = ssh_key
                changed = True
            if changed:
                server.save(update_fields=["ssh_password", "ssh_key", "updated_at"])
                logger.info(
                    "Stored SSH credentials from env for %s (%s)",
                    server.name, server.host,
                )
        else:
            logger.debug("No ManagedServer found to store SSH credentials")
    except Exception as exc:
        logger.warning("Failed to store SSH credentials from env: %s", exc)


def schedule_startup_caddy_sync():
    """Fire a one-time background sync."""
    # Always try to store SSH credentials from env vars on startup
    _store_ssh_from_env()

    if caddy_disabled_mode():
        logger.debug("Caddy-disabled mode: skipping startup caddy sync")
        return

    global _started
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_sync_caddy_once, name="caddy-sync-startup", daemon=True)
    thread.start()
