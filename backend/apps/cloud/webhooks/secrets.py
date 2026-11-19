"""Shared webhook secret resolution for setup and request authentication."""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def resolve_webhook_secret(provider: str) -> str:
    """Resolve DB/env configuration first, then legacy Django settings.

    A configuration lookup failure must not authenticate with a potentially
    stale fallback secret after an operator has rotated the database value.
    """
    from apps.deployments.models.core import PlatformConfig

    try:
        secret = PlatformConfig.load().get_webhook_secret(provider)
    except Exception:
        logger.error("Unable to resolve %s webhook configuration", provider)
        return ""
    return secret or getattr(settings, f"{provider.upper()}_WEBHOOK_SECRET", "") or ""
