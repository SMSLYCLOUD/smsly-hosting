"""
Startup utilities for deployments app.

We use a background thread to regenerate/apply the Caddyfile shortly after
Django boots so SSL/DNS stay in sync (Railway-style "just works").
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_started = False


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
            from apps.deployments.models import PlatformConfig
            from services.caddy_manager import generate_caddyfile, apply_caddyfile
    
            cfg = PlatformConfig.load()
            content = generate_caddyfile(cfg)
            token = (getattr(cfg, "cloudflare_api_token", "") or "").strip()
            result = apply_caddyfile(content, cloudflare_token=token, preserve_existing_token=True)
            logger.info("Startup Caddy sync: %s", result.get("message", "ok"))
        finally:
            try:
                os.unlink(lock_file)
            except OSError:
                pass

        # 2. Trigger Auto-Authentication for nodes missing API tokens
        try:
            from apps.deployments.tasks import auto_authenticate_nodes_task
            auto_authenticate_nodes_task.delay()
            logger.info("Startup: Triggered auto-authentication task for nodes.")
        except Exception as e:
            logger.warning("Startup: Failed to trigger auto-auth task: %s", e)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Startup background tasks failed: %s", exc)


def schedule_startup_caddy_sync():
    """Fire a one-time background sync."""
    import os
    if str(os.environ.get("MODE", "")).strip().lower() == "agent":
        logger.debug("Agent-lite mode: skipping startup caddy sync")
        return

    global _started  # noqa: PLW0603
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_sync_caddy_once, name="caddy-sync-startup", daemon=True)
    thread.start()
