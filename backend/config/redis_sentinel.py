"""
Redis Sentinel support for high-availability deployments.

When SENTINEL_HOSTS is set (e.g. "sentinel1:26379,sentinel2:26379,sentinel3:26379"),
all Redis connections are routed through Sentinel for automatic failover.

Usage in settings.py:
    SENTINEL_ENABLED = True
    SENTINEL_SERVICE_NAME = 'mymaster'

Falls back to standalone Redis when SENTINEL_HOSTS is not set.
"""
import logging
import os
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# ── Sentinel configuration from environment ──────────────────────────

def _parse_sentinel_hosts(raw: str) -> list[tuple[str, int]]:
    """Parse 'host1:port1,host2:port2' into [('host1', 26379), ...]"""
    hosts = []
    for item in raw.split(','):
        item = item.strip()
        if not item:
            continue
        if ':' in item:
            host, port = item.rsplit(':', 1)
            hosts.append((host, int(port)))
        else:
            hosts.append((item, 26379))
    return hosts


SENTINEL_HOSTS_RAW = os.environ.get('SENTINEL_HOSTS', '').strip()
SENTINEL_ENABLED = bool(SENTINEL_HOSTS_RAW)
SENTINEL_HOSTS = _parse_sentinel_hosts(SENTINEL_HOSTS_RAW) if SENTINEL_ENABLED else []
SENTINEL_SERVICE_NAME = os.environ.get('SENTINEL_SERVICE_NAME', 'mymaster')
SENTINEL_PASSWORD = os.environ.get('SENTINEL_PASSWORD', '') or None
SENTINEL_SOCKET_TIMEOUT = int(os.environ.get('SENTINEL_SOCKET_TIMEOUT', '5'))
SENTINEL_SOCKET_CONNECT_TIMEOUT = int(os.environ.get('SENTINEL_SOCKET_CONNECT_TIMEOUT', '5'))

# Module-level cache — Sentinel instance is expensive to create (opens
# connections to all sentinels) so we create it once and reuse.
_sentinel_instance = None


def get_sentinel():
    """Return a cached redis.sentinel.Sentinel instance.

    Returns None if SENTINEL_HOSTS is not configured.
    """
    global _sentinel_instance
    if not SENTINEL_ENABLED:
        return None
    if _sentinel_instance is not None:
        return _sentinel_instance
    from redis.sentinel import Sentinel
    sentinel_kwargs = {}
    if SENTINEL_PASSWORD:
        sentinel_kwargs['password'] = SENTINEL_PASSWORD
    _sentinel_instance = Sentinel(
        SENTINEL_HOSTS,
        sentinel_kwargs=sentinel_kwargs,
        socket_timeout=SENTINEL_SOCKET_TIMEOUT,
        socket_connect_timeout=SENTINEL_SOCKET_CONNECT_TIMEOUT,
    )
    return _sentinel_instance


def get_master_connection(password: str | None = None, db: int = 0):
    """Return a Redis client connected to the Sentinel-managed master.

    Falls back to a direct connection if Sentinel is not configured.
    """
    sentinel = get_sentinel()
    if sentinel is None:
        return None
    connection_kwargs = {
        'db': db,
        'socket_timeout': SENTINEL_SOCKET_TIMEOUT,
        'socket_connect_timeout': SENTINEL_SOCKET_CONNECT_TIMEOUT,
        'decode_responses': True,
    }
    if password:
        connection_kwargs['password'] = password
    return sentinel.master_for(
        SENTINEL_SERVICE_NAME,
        **connection_kwargs,
    )


def get_slave_connection(password: str | None = None, db: int = 0):
    """Return a Redis client connected to a Sentinel-managed slave (read replica).

    Falls back to a direct connection if Sentinel is not configured.
    """
    sentinel = get_sentinel()
    if sentinel is None:
        return None
    connection_kwargs = {
        'db': db,
        'socket_timeout': SENTINEL_SOCKET_TIMEOUT,
        'socket_connect_timeout': SENTINEL_SOCKET_CONNECT_TIMEOUT,
        'decode_responses': True,
    }
    if password:
        connection_kwargs['password'] = password
    return sentinel.slave_for(
        SENTINEL_SERVICE_NAME,
        **connection_kwargs,
    )


def sentinel_url(db: int = 0, password: str | None = None) -> str | None:
    """Return a redis+sentinel:// URL for use with Django/Channels.

    The password parameter should be the Redis master password (not the
    Sentinel password).  When Sentinel is not configured, returns None
    (caller should use the standalone URL instead).

    redis-py 8.0 URL format — all sentinels comma-separated:
        redis+sentinel://[username:password@]host1:port1,host2:port2[/db]
    """
    if not SENTINEL_ENABLED:
        return None
    hosts_part = ",".join(f"{h}:{p}" for h, p in SENTINEL_HOSTS)
    if password:
        return f"redis+sentinel://:{password}@{hosts_part}/{db}"
    return f"redis+sentinel://{hosts_part}/{db}"


def sentinel_url_for_db(base_url: str | None, db: int, password: str | None = None) -> str | None:
    """If Sentinel is configured, return a sentinel URL for the given DB.
    Otherwise, return the base_url with the DB number replaced."""
    s_url = sentinel_url(db, password=password)
    if s_url:
        return s_url
    if base_url:
        parsed = urlparse(base_url)
        return urlunparse(parsed._replace(path=f'/{db}'))
    return None
