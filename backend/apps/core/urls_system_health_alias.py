"""Frontend compatibility alias: /api/v1/system/health/

The frontend ``systemApi.health`` (lib/api.ts:837) calls
``GET /api/v1/system/health/`` to render the platform-status
header indicator. The canonical health probes live at
``/health``, ``/health/live`` and ``/health/ready`` (top-level,
not under /api/v1/). This alias exposes a thin ``/api/v1/system/health/``
endpoint that proxies to ``/health`` for the frontend.
"""
from config.health import health_check
from django.urls import path

urlpatterns = [
    path('', health_check, name='system-health-alias'),
]
