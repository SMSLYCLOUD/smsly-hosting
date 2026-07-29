"""
Resolves {{addon-name.KEY}} shortcodes in environment variable values.

Supported shortcode keys:
  {{addon-name.URL}}      -> full connection URL
  {{addon-name.HOST}}     -> hostname
  {{addon-name.PORT}}     -> port
  {{addon-name.USER}}     -> username
  {{addon-name.PASSWORD}} -> password
  {{addon-name.DATABASE}} -> database name

Platform shortcuts (no need to know the addon name):
  {{SMSLY.POSTGRES}}          -> full connection URL for the service's active POSTGRES addon
  {{SMSLY.POSTGRES.URL}}      -> full connection URL
  {{SMSLY.POSTGRES.HOST}}     -> hostname
  {{SMSLY.POSTGRES.PORT}}     -> port
  {{SMSLY.POSTGRES.USER}}     -> username
  {{SMSLY.POSTGRES.PASSWORD}} -> password
  {{SMSLY.POSTGRES.DATABASE}} -> database name

NOTE: For backwards compatibility, addon-name resolution still works.
"""
import logging
import re
from urllib.parse import urlparse

from apps.deployments.models.addons import Addon

logger = logging.getLogger(__name__)

PLATFORM_SHORTCODE_RE = re.compile(
    r'\{\{SMSLY\.(POSTGRES|POSTGRESS|REDIS|MYSQL|MONGODB|QDRANT|ELASTICSEARCH|RABBITMQ)(?:\.(URL|HOST|PORT|USER|PASSWORD|DATABASE))?\}\}',
    re.IGNORECASE,
)
LEGACY_SHORTCODE_RE = re.compile(
    r'\{\{([a-zA-Z0-9_.-]+)\.(URL|HOST|PORT|USER|PASSWORD|DATABASE)\}\}'
)


def _resolve_from_url(connection_url: str, key_suffix: str) -> str | None:
    url = str(connection_url or '').strip()
    if not url:
        return None

    key = str(key_suffix or '').strip().upper()
    if key == 'URL':
        return url

    parsed = urlparse(url)
    if key == 'HOST':
        return parsed.hostname or None
    if key == 'PORT':
        return str(parsed.port) if parsed.port else None
    if key == 'USER':
        return parsed.username or None
    if key == 'PASSWORD':
        return parsed.password or None
    if key == 'DATABASE':
        if parsed.path and parsed.path != '/':
            return parsed.path.lstrip('/')
        return None
    return None


def resolve_shortcodes(service_id: str, value: str) -> str:
    """Replace all {{addon-name.KEY}} shortcodes with real values."""
    if '{{' not in value:
        return value

    def platform_replacer(match):
        addon_type = str(match.group(1) or '').strip().upper()
        if addon_type == 'POSTGRESS':
            addon_type = 'POSTGRES'
        key_suffix = str(match.group(2) or 'URL').strip().upper() or 'URL'

        addon = (
            Addon.objects.filter(
                service_id=service_id,
                addon_type=addon_type,
                status='ACTIVE',
            )
            .order_by('-created_at')
            .first()
        )
        if not addon:
            logger.warning("Shortcode references missing addon type=%s for service_id=%s", addon_type, service_id)
            return match.group(0)

        resolved = _resolve_from_url(getattr(addon, 'connection_url', ''), key_suffix)
        return resolved if resolved is not None else match.group(0)

    def legacy_replacer(match):
        addon_name = match.group(1)
        key_suffix = match.group(2)
        try:
            addon = Addon.objects.get(
                service_id=service_id,
                name__iexact=addon_name,
                status='ACTIVE',
            )
        except Addon.DoesNotExist:
            logger.warning("Shortcode references unknown addon name=%s service_id=%s", addon_name, service_id)
            return match.group(0)  # leave unresolved

        resolved = _resolve_from_url(getattr(addon, 'connection_url', ''), key_suffix)
        return resolved if resolved is not None else match.group(0)

    out = PLATFORM_SHORTCODE_RE.sub(platform_replacer, value)
    return LEGACY_SHORTCODE_RE.sub(legacy_replacer, out)
