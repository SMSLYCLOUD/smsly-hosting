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
    @staticmethod
    def _get_sentinel_redis():
        from config.redis_sentinel import (
            SENTINEL_ENABLED,
            get_master_connection,
        )
        from django.conf import settings

        if not SENTINEL_ENABLED:
            return None

        password = getattr(settings, 'REDIS_PASSWORD', None)
        db = 0
        url = getattr(settings, 'CELERY_REDBEAT_REDIS_URL', None)
        if url:
            parsed = urlparse(url)
            db = int(parsed.path.lstrip('/') or 0) if parsed.path else 0
        return get_master_connection(password=password, db=db)

    def __init__(self, *args, **kwargs):
        app = kwargs.get('app') or (args[0] if args else None)
        if app is not None:
            conn = self._get_sentinel_redis()
            if conn is not None:
                # celery-redbeat reads app.redbeat_redis in get_redis(app)
                app.redbeat_redis = conn
        super().__init__(*args, **kwargs)

    def _get_redis(self):
        conn = self._get_sentinel_redis()
        if conn is not None:
            return conn
        if hasattr(super(), '_get_redis'):
            return super()._get_redis()
        return getattr(getattr(self, 'app', None), 'redbeat_redis', None)
