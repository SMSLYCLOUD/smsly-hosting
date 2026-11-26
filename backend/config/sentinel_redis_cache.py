"""
Sentinel-aware Django cache backend.

When SENTINEL_HOSTS is configured, Redis connections route through
Sentinel for automatic master failover. Falls back to standard
RedisCache (plain redis:// URL) when Sentinel is not configured.
"""
from urllib.parse import urlparse

from django.core.cache.backends.redis import RedisCache as BaseRedisCache, RedisCacheClient


class SentinelRedisCacheClient(RedisCacheClient):
    """Django 5 RedisCacheClient that routes connections via Redis Sentinel."""

    def get_client(self, key=None, *, write=False):
        from config.redis_sentinel import (
            SENTINEL_ENABLED,
            get_master_connection,
        )
        from django.conf import settings

        if SENTINEL_ENABLED:
            password = getattr(settings, 'REDIS_PASSWORD', None)
            url = getattr(settings, 'REDIS_CACHE_URL', None)
            db = 0
            if url:
                parsed = urlparse(url)
                db = int(parsed.path.lstrip('/') or 0) if parsed.path else 0
            conn = get_master_connection(password=password, db=db)
            if conn is not None:
                return conn
        return super().get_client(key, write=write)


class SentinelRedisCache(BaseRedisCache):
    def __init__(self, server, params):
        super().__init__(server, params)
        self._class = SentinelRedisCacheClient
