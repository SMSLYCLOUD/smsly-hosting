"""
Django cache backend with Redis + LocMemCache fallback.

Wraps SentinelRedisCache (Sentinel-aware Redis) and transparently
falls back to LocMemCache when Redis is unavailable. Reconnection
is attempted periodically; when Redis recovers, the backend
switches back automatically.

Usage in settings.py:
    CACHES = {
        "default": {
            "BACKEND": "config.fallback_redis_cache.FallbackRedisCache",
            "LOCATION": REDIS_CACHE_URL,
            "OPTIONS": { ... },
        }
    }
"""
import logging
import time

from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)

# How often to attempt reconnection after Redis failure (seconds).
_RECONNECT_INTERVAL = 60


class FallbackRedisCache(RedisCache):
    """Redis-backed cache that falls back to LocMemCache on failure."""

    def __init__(self, server, params):
        super().__init__(server, params)
        self._fallback = LocMemCache(server, params)
        self._maybe_use_sentinel()
        self._degraded = False
        self._last_redis_failure: float = 0
        self._redis_healthy = True

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    def _maybe_use_sentinel(self):
        """Swap the underlying Redis connection pool for a Sentinel-managed
        one when SENTINEL_HOSTS is configured.

        Django 5.0's RedisCache uses a RedisCacheClient which wraps a
        redis.Redis instance.  We replace that inner client's connection
        pool with a SentinelConnectionPool so writes route to the
        current master automatically.
        """
        from config.redis_sentinel import SENTINEL_ENABLED, get_master_connection
        if not SENTINEL_ENABLED:
            return
        from django.conf import settings
        password = getattr(settings, 'REDIS_PASSWORD', None)
        url = getattr(settings, 'REDIS_CACHE_URL', None)
        db = 0
        if url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            db = int(parsed.path.lstrip('/') or 0) if parsed.path else 0
        try:
            sentinel_client = get_master_connection(password=password, db=db)
            if sentinel_client is None:
                return
            # Replace the connection pool on the RedisCacheClient's inner
            # redis.Redis instance so the cache API (set/get/delete) works.
            inner = getattr(self._cache, '_client', None) or self._cache
            if hasattr(inner, 'connection_pool'):
                inner.connection_pool = sentinel_client.connection_pool
                logger.info(
                    "Cache backend using Sentinel master for db=%d", db,
                )
        except Exception as exc:
            logger.warning(
                "Sentinel unavailable for cache, falling back to direct: %s",
                exc,
            )

    # -- internal helpers ---------------------------------------------------

    def _should_attempt_reconnect(self) -> bool:
        if not self._degraded:
            return False
        return (time.time() - self._last_redis_failure) >= _RECONNECT_INTERVAL

    def _enter_degraded(self, exc: Exception):
        if not self._degraded:
            logger.warning(
                "Redis unavailable, falling back to LocMemCache: %s", exc,
            )
        self._degraded = True
        self._redis_healthy = False
        self._last_redis_failure = time.time()

    def _exit_degraded(self):
        if self._degraded:
            logger.warning("Redis recovered, exiting degraded mode")
        self._degraded = False
        self._redis_healthy = True

    def _call_with_fallback(self, method: str, *args, **kwargs):
        """Try Redis first; on failure, delegate to LocMemCache."""
        if self._should_attempt_reconnect():
            try:
                result = getattr(super(), method)(*args, **kwargs)
                self._exit_degraded()
                return result
            except Exception as exc:
                self._enter_degraded(exc)
                return getattr(self._fallback, method)(*args, **kwargs)

        if self._degraded:
            return getattr(self._fallback, method)(*args, **kwargs)

        try:
            result = getattr(super(), method)(*args, **kwargs)
            return result
        except Exception as exc:
            self._enter_degraded(exc)
            return getattr(self._fallback, method)(*args, **kwargs)

    # -- cache API ----------------------------------------------------------

    def add(self, key, value, timeout=None, **kwargs):
        return self._call_with_fallback("add", key, value, timeout=timeout, **kwargs)

    def get(self, key, default=None):
        return self._call_with_fallback("get", key, default)

    def set(self, key, value, timeout=None, **kwargs):
        return self._call_with_fallback("set", key, value, timeout=timeout, **kwargs)

    def delete(self, key):
        return self._call_with_fallback("delete", key)

    def has_key(self, key):
        return self._call_with_fallback("has_key", key)

    def incr(self, key, delta=1):
        return self._call_with_fallback("incr", key, delta)

    def decr(self, key, delta=1):
        return self._call_with_fallback("decr", key, delta)

    def get_many(self, keys):
        return self._call_with_fallback("get_many", keys)

    def set_many(self, mapping, timeout=None, **kwargs):
        return self._call_with_fallback("set_many", mapping, timeout=timeout, **kwargs)

    def delete_many(self, keys):
        return self._call_with_fallback("delete_many", keys)

    def clear(self):
        return self._call_with_fallback("clear")

    def close(self):
        try:
            super().close()
        except Exception:
            pass
        try:
            self._fallback.close()
        except Exception:
            pass
