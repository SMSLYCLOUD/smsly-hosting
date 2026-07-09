import logging

logger = logging.getLogger(__name__)
import logging

from apps.deployments.models import (  # type: ignore[attr-defined]
    PlatformConfig,
)


def _regenerate_caddyfile():
    """Regenerate and apply the Caddyfile with current service domains.

    Called after successful deployments so new services get Caddy site blocks
    (and therefore SSL certificates) without requiring a manual Settings save.
    """
    try:
        config = PlatformConfig.load()
        from services.caddy_manager import apply_caddyfile, generate_caddyfile
        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            logger.info("Caddyfile regenerated after deployment")
        else:
            logger.warning("Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not regenerate Caddyfile: %s", exc)
