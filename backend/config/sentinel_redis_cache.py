"""
Sentinel-aware Django cache backend.

When SENTINEL_HOSTS is configured, Redis connections route through
Sentinel for automatic master failover. Falls back to standard
RedisCache (plain redis:// URL) when Sentinel is not configured.
"""
from urllib.parse import urlparse

from django.core.cache.backends.redis import RedisCache as BaseRedisCache


class SentinelRedisCache(BaseRedisCache):
    def _connect(self):
        from config.redis_sentinel import (
            SENTINEL_ENABLED,
            get_master_connection,
        )
        from django.conf import settings

        password = getattr(settings, 'REDIS_PASSWORD', None)
        url = getattr(settings, 'REDIS_CACHE_URL', None)

        if SENTINEL_ENABLED:
            db = 0
            if url:
                parsed = urlparse(url)
                db = int(parsed.path.lstrip('/') or 0) if parsed.path else 0
            conn = get_master_connection(password=password, db=db)
            if conn is not None:
                return conn

        import redis
        if url:
            return redis.from_url(url, **self._options)

        raise ConnectionError(
            "REDIS_CACHE_URL not configured and Sentinel unavailable"
        )
