"""Gunicorn configuration with safe Copy-on-Write app preloading.

Preloading Django/ASGI before forking shares read-only Python bytecode and
framework memory pages across all UvicornWorker processes (~90-110 MB RSS
saved on 2-4 workers). ``SMSLY_DEFER_POST_FORK=1`` suppresses background
thread/socket creation in ``DeploymentsConfig.ready()`` inside the master
process, and ``post_fork`` closes inherited DB connections and starts the
deferred Caddy sync thread inside forked workers.
"""

import os

preload_app = (
    os.environ.get("GUNICORN_PRELOAD", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "2000"))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", "200"))

if preload_app:
    os.environ["SMSLY_DEFER_POST_FORK"] = "1"


def post_fork(server, worker):
    """Clean inherited DB handles and run deferred worker startup hooks."""
    os.environ.pop("SMSLY_DEFER_POST_FORK", None)
    try:
        from django.db import connections

        connections.close_all()
    except Exception:
        pass
    try:
        from django.conf import settings
        from apps.deployments.apps import _is_serving_process

        if not getattr(settings, "IS_TESTING", False) and _is_serving_process():
            from apps.deployments.services.startup import schedule_startup_caddy_sync

            schedule_startup_caddy_sync()
    except Exception:
        pass
