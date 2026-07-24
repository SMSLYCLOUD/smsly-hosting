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

The login view also stamps the auth token as an HttpOnly+SameSite=Strict
cookie on the response so the frontend can drop the legacy
``Authorization: Token <key>`` header and rely on
``withCredentials: true`` to have the browser attach the cookie
automatically. The token is still returned in the JSON body, so
existing API clients that read the body keep working unchanged.

The wire-up in ``config/urls.py``:

    path('api/v1/auth/login/', ThrottledLoginView.as_view()),
    path('api/v1/auth/logout/', ThrottledLogoutView.as_view()),
    path('api/v1/auth/password/reset/', ThrottledPasswordResetView.as_view()),
    path('api/v1/auth/registration/', ThrottledRegistrationView.as_view()),

replaces the corresponding ``dj_rest_auth`` URLs.
"""
from apps.core.auth_cookies import delete_auth_cookie, set_auth_cookie
from apps.core.rate_limiting import (
    LoginRateThrottle,
    PasswordResetRateThrottle,
    RegistrationRateThrottle,
)
from dj_rest_auth.registration.views import (
    RegisterView as _BaseRegistrationView,
)
from dj_rest_auth.views import (
    LoginView as _BaseLoginView,
)
from dj_rest_auth.views import (
    LogoutView as _BaseLogoutView,
)
from dj_rest_auth.views import (
    PasswordResetView as _BasePasswordResetView,
)


class ThrottledLoginView(_BaseLoginView):
    """``POST /api/v1/auth/login/`` with brute-force guard.

    10/min per IP matches the platform's intent for login:
    a forgetful user can mistype a few times; an attacker
    cannot spray 278 passwords/sec.

    Also sets the auth token as an HttpOnly+SameSite=Strict cookie
    on a successful response. The cookie carries the same token that
    is returned in the JSON body (``{"key": "..."}``), so existing
    API clients that read the body keep working.
    """
    throttle_classes = [LoginRateThrottle]

    def get_response(self):
        response = super().get_response()
        # ``self.token`` is populated by ``dj_rest_auth``'s ``login()``
        # and is the canonical DRF Token instance. We only stamp the
        # cookie on a successful login (status 200) — failure responses
        # have no token and would clear any prior session cookie.
        if response.status_code == 200 and getattr(self, "token", None):
            try:
                set_auth_cookie(response, self.token.key)
            except Exception:
                # Never let a cookie helper crash the login response.
                # Worst case the user can re-authenticate or the frontend
                # falls back to the body-returned token via the legacy
                # ``Authorization: Token`` header.
                pass
        return response


class ThrottledLogoutView(_BaseLogoutView):
    """``POST /api/v1/auth/logout/`` that clears the HttpOnly auth cookie.

    ``dj_rest_auth``'s ``LogoutView`` already deletes the server-side
    token (``request.user.auth_token.delete()``) when SESSION_LOGIN is
    enabled. This subclass additionally clears the HttpOnly cookie on
    the response so the browser stops sending it on the next request.
    """
    throttle_classes = [LoginRateThrottle]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        try:
            delete_auth_cookie(response)
        except Exception:
            # If the cookie helper raises, the server-side token has
            # already been deleted and the frontend can still clear its
            # own state on the next page load.
            pass
        return response


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
