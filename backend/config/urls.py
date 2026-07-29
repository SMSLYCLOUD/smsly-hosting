"""Urls module."""
from apps.addons.views_crud import toggle_bucket_public_api
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from config.health import health_check, health_check_verbose, liveness_check, readiness_check

urlpatterns = [
    # ─── CRITICAL: Direct Addon Actions (Greedy Regex bypass for router shadowing) ───
    re_path(r'^api/v1/addons/(?P<pk>[^/.]+)/toggle_bucket_public/?$', toggle_bucket_public_api, name='addon-toggle-bucket-public-root'),
    # path('admin/', admin.site.urls), # Moved to conditional block below


    # Health probes
    path('health', health_check, name='health-check'),
    path('health/live', liveness_check, name='health-liveness'),
    path('health/ready', readiness_check, name='health-readiness'),
    path('health/verbose', health_check_verbose, name='health-verbose'),
    path('healthz', health_check, name='healthz-check'),
    path('', include('django_prometheus.urls')),

    # ─── Frontend compatibility aliases (MUST come before the broad
    # ``path('api/v1/', include('apps.deployments.urls'))`` so the
    # /api/v1/<anything>/ matchers in deployments.urls do not eat
    # these paths first and 404 the frontend.
    path('api/v1/dashboard/overview/',
         include('apps.core.urls.dashboard_alias')),
    path('api/v1/system/health/',
         include('apps.core.urls.system_health_alias')),
    path('api/v1/system/resources/',
         include('apps.core.urls.system_resources_alias')),
    path('api/v1/preferences/',
         include('apps.notifications.urls.preferences_alias')),
    path('api/v1/ecosystem/bulk-update-environment/',
         include('apps.cloud.urls.ecosystem_bulk_env_alias')),
    path('api/v1/ecosystem/cached-scan/',
         include('apps.cloud.urls.ecosystem_cached_scan_alias')),
    path('api/v1/resource-alerts/',
         include('apps.notifications.urls.resource_alerts_alias')),
    path('api/v1/api-keys/',
         include('apps.core.urls.api_keys_alias')),
    path('api/v1/admin/users/',
         include('apps.core.urls.admin_users_alias')),
    path('api/v1/observability/',
         include('apps.core.urls.observability_alias')),
    # OAuth callback aliases — the frontend uses
    # /api/v1/accounts/<provider>/login/ but allauth is mounted
    # at /accounts/<provider>/login/. The alias re-exports
    # allauth.urls under the /api/v1/accounts/ prefix.
    path('api/v1/accounts/',
         include('apps.deployments.urls.accounts_alias')),

    # API
    path('api/v1/', include('apps.deployments.urls')),
    path('api/v1/cloud/', include('apps.cloud.urls')),
    path('api/v1/teams/', include('apps.teams.urls')),
    path('api/v1/autoscaler/', include('apps.autoscaler.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/v1/core/', include('apps.core.urls')),
    path('api/v1/domains/', include('apps.domains.urls')),
    path('api/v1/organizations/', include('apps.organizations.urls')),

    # Auth — the three brute-force-sensitive endpoints
    # (login, password reset, registration) are mounted from
    # a throttled URL conf that subclasses dj_rest_auth's
    # views and applies the platform's narrow auth throttles.
    # The remaining dj_rest_auth URLs (logout, user details,
    # password change) are mounted via the existing include
    # below and keep their default global throttle. The
    # throttled URLs MUST come first so they win the URL
    # resolution race.
    path('api/v1/auth/', include('apps.core.urls.throttled_auth')),
    path('api/v1/auth/', include('dj_rest_auth.urls')),
    path('api/v1/auth/2fa/', include('apps.deployments.urls.2fa')),
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

if 'apps.addons' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/addons/', include('apps.addons.urls')))

if 'apps.intelligence' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/ai/', include('apps.intelligence.urls')))
    urlpatterns.append(path('api/v1/', include('apps.intelligence.urls.openai')))

if 'apps.media' in settings.INSTALLED_APPS:
    urlpatterns.append(path('api/v1/media/', include('apps.media.urls')))

# ─── Server Identity Attestation (Zero-Trust challenge-response) ──────────
try:
    from apps.deployments.views.attestation import (
        attestation_challenge,
        attestation_verify,
    )
    urlpatterns += [
        path('api/v1/internal/attest/challenge/', attestation_challenge, name='attest-challenge'),
        path('api/v1/internal/attest/verify/', attestation_verify, name='attest-verify'),
    ]
except ImportError:
    pass

# ─── Tunnel API (function-based views, not DRF router) ────────────────────
try:
    if getattr(settings, 'ENABLE_LEGACY_TUNNEL_API', False):
        from apps.deployments.services.tunnels.api import get_urlpatterns as tunnel_urls
        urlpatterns += [path('api/v1/legacy/', include(tunnel_urls()))]
except ImportError:
    pass  # tunnels module not installed

# ─── OpenAPI Schema & Docs ────────────────────────────────────────────
# SECURITY: Only expose schema/docs in DEBUG mode to prevent API surface
# enumeration in production. Set DEBUG=True temporarily to access docs.
if settings.DEBUG:
    urlpatterns += [
        path('api/docs/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/swagger/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/docs/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    ]
