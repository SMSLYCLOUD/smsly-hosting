"""
Authentication views with platform-specific rate limits.

The bundled ``dj_rest_auth`` views (``LoginView``,
``PasswordResetView``, ``UserDetailsView``, etc.) inherit
``GenericAPIView`` and use the global ``DEFAULT_THROTTLE_CLASSES``
(``UserRateThrottle`` and ``AnonRateThrottle``). On SMSLY the
default user rate is 1,000,000/hour — effectively unlimited
(≈278 req/sec). For login that defeats the entire point of
the brute-force guard; a single attacker can spray 278
passwords per second against one account.

This module subclasses the bundled auth views to apply the
narrow ``LoginRateThrottle`` (10/min/IP) and
``PasswordResetRateThrottle`` (5/hour/IP) to the
brute-force-sensitive endpoints while leaving the bundled
``UserDetailsView``, ``LogoutView`` etc. on the global throttle
(those are authenticated and thus less abusable).

The wire-up in ``config/urls.py``:

    path('api/v1/auth/login/', ThrottledLoginView.as_view()),
    path('api/v1/auth/password/reset/', ThrottledPasswordResetView.as_view()),
    path('api/v1/auth/registration/', ThrottledRegistrationView.as_view()),

replaces the corresponding ``dj_rest_auth`` URLs.
"""
from dj_rest_auth.views import (
    LoginView as _BaseLoginView,
    PasswordResetView as _BasePasswordResetView,
)
from dj_rest_auth.registration.views import (
    RegisterView as _BaseRegistrationView,
)

from apps.deployments.rate_limiting import (
    LoginRateThrottle,
    PasswordResetRateThrottle,
    RegistrationRateThrottle,
)


class ThrottledLoginView(_BaseLoginView):
    """``POST /api/v1/auth/login/`` with brute-force guard.

    10/min per IP matches the platform's intent for login:
    a forgetful user can mistype a few times; an attacker
    cannot spray 278 passwords/sec.
    """
    throttle_classes = [LoginRateThrottle]


class ThrottledPasswordResetView(_BasePasswordResetView):
    """``POST /api/v1/auth/password/reset/`` with email-bomb guard.

    5/hour per IP caps the rate at which an attacker can spam
    reset emails to a victim (which is itself a denial-of-service
    attack against the victim's inbox).
    """
    throttle_classes = [PasswordResetRateThrottle]


class ThrottledRegistrationView(_BaseRegistrationView):
    """``POST /api/v1/auth/registration/`` with bot-account guard.

    5/hour per IP matches the platform's billing-enforced
    account-creation cap. Operators who need bulk accounts use
    the admin CLI.
    """
    throttle_classes = [RegistrationRateThrottle]
