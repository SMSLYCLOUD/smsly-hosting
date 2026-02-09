"""Urls module."""
from django.contrib import admin
from django.urls import path, include
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

    # Auth
    path('api/v1/auth/', include('dj_rest_auth.urls')),
    path(
        'api/v1/auth/registration/',
        include('dj_rest_auth.registration.urls')),
    path('accounts/', include('allauth.urls')),
]
