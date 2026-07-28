from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _regenerate_caddyfile():
    try:
        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()
        from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile
        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            logger.info("Caddyfile regenerated after deployment")
        else:
            logger.warning("Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:
        logger.warning("Could not regenerate Caddyfile: %s", exc)
