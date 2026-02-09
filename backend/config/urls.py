"""Urls module."""
from django.contrib import admin
from django.urls import path, include
from .health import health


urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),  # OAuth social login
    path('api/v1/auth/', include('dj_rest_auth.urls')),  # REST auth endpoints
    path(
        'api/v1/auth/registration/',
        include('dj_rest_auth.registration.urls')),
    # Registration
    path('api/v1/cloud/', include('apps.cloud.urls')),
    path('api/v1/', include('apps.deployments.urls')),
    path('api/v1/', include('apps.teams.urls')),
    path('api/v1/billing/', include('apps.billing.urls')),
    path('health', health, name='health'),  # Health check for load balancers
]
