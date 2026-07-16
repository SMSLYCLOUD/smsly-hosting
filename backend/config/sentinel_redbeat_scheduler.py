"""
Sentinel-aware RedBeat scheduler.

When SENTINEL_HOSTS is configured, the RedBeat scheduler uses a
Sentinel-managed Redis connection for automatic master failover.
Falls back to the standard RedBeatScheduler when Sentinel is not
configured.
"""
from urllib.parse import urlparse

from redbeat.schedulers import RedBeatScheduler as BaseRedBeatScheduler


class SentinelRedBeatScheduler(BaseRedBeatScheduler):
    def _get_redis(self):
        from config.redis_sentinel import (
            SENTINEL_ENABLED,
            get_master_connection,
        )
        from django.conf import settings

        password = getattr(settings, 'REDIS_PASSWORD', None)

        if SENTINEL_ENABLED:
            db = 0
            url = getattr(settings, 'CELERY_REDBEAT_REDIS_URL', None)
            if url:
                parsed = urlparse(url)
                db = int(parsed.path.lstrip('/') or 0) if parsed.path else 0
            conn = get_master_connection(password=password, db=db)
            if conn is not None:
                return conn

        return super()._get_redis()
