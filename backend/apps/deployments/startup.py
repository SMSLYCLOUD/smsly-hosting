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
        from apps.deployments.models import PlatformConfig
        from services.caddy_manager import generate_caddyfile, apply_caddyfile

        cfg = PlatformConfig.load()
        content = generate_caddyfile(cfg)
        token = (getattr(cfg, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=token, preserve_existing_token=True)
        logger.info("Startup Caddy sync: %s", result.get("message", "ok"))

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
    global _started  # noqa: PLW0603
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_sync_caddy_once, name="caddy-sync-startup", daemon=True)
    thread.start()
