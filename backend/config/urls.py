"""Urls module."""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from config.health import health_check, liveness_check, readiness_check
from apps.deployments.views_addons import toggle_bucket_public_api


urlpatterns = [
    # ─── CRITICAL: Direct Addon Actions (Greedy Regex bypass for router shadowing) ───
    re_path(r'^api/v1/addons/(?P<pk>[^/.]+)/toggle_bucket_public/?$', toggle_bucket_public_api, name='addon-toggle-bucket-public-root'),
    # path('admin/', admin.site.urls), # Moved to conditional block below


    # Health probes
    path('health', health_check, name='health-check'),
    path('health/live', liveness_check, name='health-liveness'),
    path('health/ready', readiness_check, name='health-readiness'),
    path('metrics', include('django_prometheus.urls')),

    # API
    path('api/v1/', include('apps.deployments.urls')),
    path('api/v1/cloud/', include('apps.cloud.urls')),
    path('api/v1/teams/', include('apps.teams.urls')),
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

# ─── Conditional App Routes (Agent Mode Resiliency) ───────────────────────
if 'django.contrib.admin' in settings.INSTALLED_APPS:
    # Defensively check if admin is already in urlpatterns to avoid W005 warning
    if not any(getattr(p, 'app_name', None) == 'admin' for p in urlpatterns):
        urlpatterns.insert(1, path('admin/', admin.site.urls))



if 'apps.billing' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/billing/', include('apps.billing.urls')))

if 'apps.licensing' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/licensing/', include('apps.licensing.urls')))

if 'apps.intelligence' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/ai/', include('apps.intelligence.urls')))
    urlpatterns.append(path('api/v1/', include('apps.intelligence.urls_openai')))

# ─── Server Identity Attestation (Zero-Trust challenge-response) ──────────
try:
    from apps.deployments.views_attestation import attestation_challenge, attestation_verify
    urlpatterns += [
        path('api/v1/internal/attest/challenge/', attestation_challenge, name='attest-challenge'),
        path('api/v1/internal/attest/verify/', attestation_verify, name='attest-verify'),
    ]
except ImportError:
    pass

# ─── Tunnel API (function-based views, not DRF router) ────────────────────
try:
    if getattr(settings, 'ENABLE_LEGACY_TUNNEL_API', False):
        from services.tunnels.api import get_urlpatterns as tunnel_urls
        urlpatterns += [path('api/v1/legacy/', include(tunnel_urls()))]
except ImportError:
    pass  # tunnels module not installed
