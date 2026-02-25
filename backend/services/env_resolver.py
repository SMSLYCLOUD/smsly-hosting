"""
Resolves {{addon-name.KEY}} shortcodes in environment variable values.

Supported shortcode keys:
  {{addon-name.URL}}      -> full connection URL
  {{addon-name.HOST}}     -> hostname
  {{addon-name.PORT}}     -> port
  {{addon-name.USER}}     -> username
  {{addon-name.PASSWORD}} -> password
  {{addon-name.DATABASE}} -> database name
"""
import re
import logging
from apps.deployments.models_addons import Addon

logger = logging.getLogger(__name__)

SHORTCODE_RE = re.compile(r'\{\{([a-zA-Z0-9_-]+)\.(URL|HOST|PORT|USER|PASSWORD|DATABASE)\}\}')


def resolve_shortcodes(service_id: str, value: str) -> str:
    """Replace all {{addon-name.KEY}} shortcodes with real values."""
    if '{{' not in value:
        return value

    def replacer(match):
        addon_name = match.group(1)
        key_suffix = match.group(2)
        try:
            addon = Addon.objects.get(
                service_id=service_id,
                name__iexact=addon_name,
                status='ACTIVE',
            )
        except Addon.DoesNotExist:
            logger.warning(f"Shortcode references unknown addon: {addon_name}")
            return match.group(0)  # leave unresolved

        creds = addon.parsed_credentials
        slug = addon.name.upper().replace('-', '_').replace(' ', '_')
        full_key = f'{slug}_{key_suffix}'
        resolved = creds.get(full_key, match.group(0))
        return resolved

    return SHORTCODE_RE.sub(replacer, value)
