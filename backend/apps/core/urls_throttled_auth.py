"""Throttled auth URL conf.

Replaces the bundled ``dj_rest_auth.urls`` and
``dj_rest_auth.registration.urls`` for the three
brute-force-sensitive endpoints (login, password reset,
registration) with subclasses that apply the platform's
narrow auth throttles.

The remaining ``dj_rest_auth`` URLs (logout, user details,
password change) are mounted via the existing
``path('api/v1/auth/', include('dj_rest_auth.urls'))`` route
and keep their default global throttle (which is appropriate —
those endpoints are authenticated and less abusable).
"""
from django.urls import path

from apps.core.views_throttled_auth import (
    ThrottledLoginView,
    ThrottledPasswordResetView,
    ThrottledRegistrationView,
)

urlpatterns = [
    path('login/', ThrottledLoginView.as_view(), name='rest_login_throttled'),
    path(
        'password/reset/',
        ThrottledPasswordResetView.as_view(),
        name='rest_password_reset_throttled',
    ),
    path(
        'registration/',
        ThrottledRegistrationView.as_view(),
        name='rest_register_throttled',
    ),
]
