"""
Ecosystem Graph — Live Service Dependency Resolution.

Queries the CloudNeuron Service DB to build a graph of all deployed
services belonging to the same owner. Used by the Autonomous Linker
in tasks.py to resolve cross-service URLs, shared addons, and
propagate secrets at deploy time.
"""

import logging
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


def build_ecosystem_graph(service) -> dict[str, Any]:
    """
    Build a live graph of all services in the same ecosystem.

    Returns:
        {
            'deployed': {
                'smsly-backend': {
                    'name': 'smsly-backend',
                    'domain': 'api.smsly.com',
                    'port': 8001,
                    'container': 'smsly-backend',
                    'addons': {'POSTGRES': 'postgres://...', 'REDIS': 'redis://...'},
                },
                ...
            },
            'shared_addons': {
                'POSTGRES': 'postgres://...',
                'REDIS': 'redis://...',
            },
        }
    """
    from apps.deployments.models import Service  # type: ignore[attr-defined]

    if not service.owner:
        logger.debug("Service %s has no owner — skipping ecosystem graph", service.name)
        return {'deployed': {}, 'shared_addons': {}}

    siblings = (
        Service.objects
        .filter(owner=service.owner)
        .exclude(id=service.id)
        .prefetch_related('addons', 'env_vars')
    )

    graph: dict[str, Any] = {
        'deployed': {},
        'shared_addons': {},
    }

    for sib in siblings:
        # Only include services that have had at least one successful deploy
        has_active = sib.deployments.filter(status='ACTIVE').exists()
        if not has_active:
            continue

        sib_info = {
            'name': sib.name,
            'domain': sib.public_domain or '',
            'port': sib.internal_port,
            'container': getattr(sib, 'container_name', '') or _slugify(sib.name),
            'addons': {},
        }

        for addon in sib.addons.filter(status='ACTIVE'):
            conn_url = addon.connection_url or ''
            sib_info['addons'][addon.addon_type] = conn_url
            if addon.addon_type not in graph['shared_addons'] and conn_url:
                graph['shared_addons'][addon.addon_type] = conn_url
            elif addon.addon_type in graph['shared_addons'] and conn_url:
                # Multiple addons of same type — check if current service has a preference
                current_addon_urls = {a.connection_url for a in service.addons.filter(status='ACTIVE', addon_type=addon.addon_type) if a.connection_url}
                if current_addon_urls and conn_url in current_addon_urls:
                    # This sibling's addon matches our own — prefer it
                    graph['shared_addons'][addon.addon_type] = conn_url
                    logger.info(
                        "Ecosystem graph: preferred addon %s from '%s' (matches current service)",
                        addon.addon_type, sib.name,
                    )
                else:
                    logger.warning(
                        "Ecosystem graph: duplicate addon %s from '%s' ignored (keeping '%s')",
                        addon.addon_type, sib.name, graph['shared_addons'].get(addon.addon_type, ''),
                    )

        graph['deployed'][sib.name] = sib_info

    # Also check the current service's own addons for shared infra
    for addon in service.addons.filter(status='ACTIVE'):
        conn_url = addon.connection_url or ''
        if addon.addon_type not in graph['shared_addons'] and conn_url:
            graph['shared_addons'][addon.addon_type] = conn_url

    logger.info(
        "Ecosystem graph for '%s': %d deployed siblings, %d shared addons",
        service.name, len(graph['deployed']), len(graph['shared_addons']),
    )
    return graph


def get_sibling_env_value(service, sibling_name: str, key: str) -> str | None:
    """
    Retrieve an environment variable from a deployed sibling service.
    Used to propagate shared secrets (e.g., INTERNAL_API_SECRET).
    """
    from apps.deployments.models import EnvironmentVariable, Service  # type: ignore[attr-defined]

    try:
        sib = Service.objects.get(name=sibling_name, owner=service.owner)
        ev = EnvironmentVariable.objects.filter(service=sib, key=key).first()
        return ev.value if ev else None
    except Service.DoesNotExist:
        return None


def find_sibling_by_pattern(graph: dict, pattern: str) -> dict | None:
    """
    Find a deployed sibling whose name matches a pattern.
    E.g., pattern='backend' matches 'smsly-backend'.
    """
    pattern_lower = pattern.lower()
    for name, info in graph.get('deployed', {}).items():
        if pattern_lower in name.lower():
            return info
    return None


def resolve_service_url(sib_info: dict, prefer_public: bool = True) -> str:
    """
    Build the best URL for a sibling service.
    Prefers HTTPS public domain, falls back to internal container:port.
    """
    if prefer_public and sib_info.get('domain'):
        return f"https://{sib_info['domain']}"
    container = sib_info.get('container') or sib_info.get('name', 'localhost')
    port = sib_info.get('port', 8000)
    return f"http://{container}:{port}"


def rewrite_database_url(base_url: str, db_name: str,
                         db_user: str | None = None,
                         db_password: str | None = None) -> str:
    """
    Rewrite a DATABASE_URL to target a specific database on the same server.
    E.g., postgres://postgres:pass@pgcat:5432/smsly_backend
          → postgres://marketer:pass@pgcat:5432/marketer
    """
    try:
        parsed = urlparse(base_url)
        # Replace path (database name)
        new_path = f"/{db_name}"
        # Optionally replace user/password
        netloc = parsed.hostname or 'localhost'
        port = parsed.port or 5432
        user = db_user or parsed.username or 'postgres'
        password = db_password or parsed.password or ''
        userinfo = f"{user}:{password}" if password else user
        new_netloc = f"{userinfo}@{netloc}:{port}"
        return urlunparse((
            parsed.scheme or 'postgresql',
            new_netloc,
            new_path,
            '', '', '',
        ))
    except Exception as exc:
        logger.warning("Failed to rewrite DATABASE_URL: %s", exc)
        return base_url


def next_available_redis_db(graph: dict, current_service_name: str) -> int:
    """
    Determine the next available Redis DB number.
    Scans all deployed siblings' REDIS_URL for their /N suffix.
    """
    used_dbs = set()
    for _name, info in graph.get('deployed', {}).items():
        redis_url = info.get('addons', {}).get('REDIS', '')
        if redis_url:
            try:
                parsed = urlparse(redis_url)
                db_num = int(parsed.path.lstrip('/') or '0')
                used_dbs.add(db_num)
            except (ValueError, AttributeError):
                pass

    # Find first unused DB number (0-15)
    for i in range(16):
        if i not in used_dbs:
            return i
    return 0  # fallback


def set_redis_db(redis_url: str, db_num: int) -> str:
    """Replace the database number in a Redis URL."""
    try:
        parsed = urlparse(redis_url)
        return urlunparse((
            parsed.scheme or 'redis',
            parsed.netloc,
            f"/{db_num}",
            '', '', '',
        ))
    except Exception:
        return redis_url


def _slugify(name: str) -> str:
    """Convert a service name to a Docker-safe container name."""
    return re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')
