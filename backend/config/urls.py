"""Urls module."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from config.health import health_check, liveness_check, readiness_check


urlpatterns = [
    path('admin/', admin.site.urls),

    # Health probes
    path('health', health_check, name='health-check'),
    path('health/live', liveness_check, name='health-liveness'),
    path('health/ready', readiness_check, name='health-readiness'),

    # API
    path('api/v1/', include('apps.deployments.urls')),
    path('api/v1/cloud/', include('apps.cloud.urls')),
    path('api/v1/teams/', include('apps.teams.urls')),
    path('api/v1/billing/', include('apps.billing.urls')),
    path('api/v1/licensing/', include('apps.licensing.urls')),
    path('api/v1/ai/', include('apps.intelligence.urls')),
    path('api/v1/autoscaler/', include('apps.autoscaler.urls')),
    path('api/v1/', include('apps.notifications.urls')),
    path('api/v1/', include('apps.core.urls')),

    # Auth
    path('api/v1/auth/', include('dj_rest_auth.urls')),
    path(
        'api/v1/auth/registration/',
        include('dj_rest_auth.registration.urls')),
    path('accounts/', include('allauth.urls')),
]

# ─── Tunnel API (function-based views, not DRF router) ────────────────────
try:
    if getattr(settings, 'ENABLE_LEGACY_TUNNEL_API', False):
        from services.tunnels.api import get_urlpatterns as tunnel_urls
        urlpatterns += [path('api/v1/legacy/', include(tunnel_urls()))]
except ImportError:
    pass  # tunnels module not installed
