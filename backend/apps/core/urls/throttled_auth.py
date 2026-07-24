"""Throttled auth URL conf.

Replaces the bundled ``dj_rest_auth.urls`` and
``dj_rest_auth.registration.urls`` for the four
brute-force- and cookie-stamp-sensitive endpoints (login, logout,
password reset, registration) with subclasses that apply the
platform's narrow auth throttles and HttpOnly cookie handling.

The remaining ``dj_rest_auth`` URLs (user details, password change)
are mounted via the existing
``path('api/v1/auth/', include('dj_rest_auth.urls'))`` route
and keep their default global throttle (which is appropriate —
those endpoints are authenticated and less abusable).
"""
from apps.core.views.throttled_auth import (
    ThrottledLoginView,
    ThrottledLogoutView,
    ThrottledPasswordResetView,
    ThrottledRegistrationView,
)
from django.urls import path

urlpatterns = [
    path('login/', ThrottledLoginView.as_view(), name='rest_login_throttled'),
    path('logout/', ThrottledLogoutView.as_view(), name='rest_logout_throttled'),
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
