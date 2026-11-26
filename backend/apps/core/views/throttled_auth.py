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
automatically. The body token is omitted unless
``SMSLY_TOKEN_IN_BODY=true`` (legacy API clients that read the body).

The wire-up in ``config/urls.py``:

    path('api/v1/auth/login/', ThrottledLoginView.as_view()),
    path('api/v1/auth/logout/', ThrottledLogoutView.as_view()),
    path('api/v1/auth/password/reset/', ThrottledPasswordResetView.as_view()),
    path('api/v1/auth/registration/', ThrottledRegistrationView.as_view()),

replaces the corresponding ``dj_rest_auth`` URLs.
"""
from apps.core.auth_2fa import (
    admin_2fa_enrollment_required,
    clear_pending_2fa,
    stash_pending_2fa,
    user_requires_2fa,
)
from apps.core.auth_cookies import delete_auth_cookie, set_auth_cookie
from apps.core.rate_limiting import (
    LoginRateThrottle,
    PasswordResetRateThrottle,
    RegistrationRateThrottle,
)
from django.conf import settings
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
from rest_framework.response import Response


class ThrottledLoginView(_BaseLoginView):
    """``POST /api/v1/auth/login/`` with brute-force guard.

    10/min per IP matches the platform's intent for login:
    a forgetful user can mistype a few times; an attacker
    cannot spray 278 passwords/sec.

    Also sets the auth token as an HttpOnly+SameSite=Strict cookie
    on a successful response. The cookie carries the same token that
    used to be returned in the JSON body — the body copy is omitted
    unless ``SMSLY_TOKEN_IN_BODY=true`` (legacy clients).

    2FA ENFORCEMENT: when the authenticated user has a confirmed TOTP
    device, this view issues NOTHING — no DRF token, no session login,
    no cookie. It stashes the pending identity in the session and
    returns ``{"requires_2fa": true}``. The client must complete
    ``POST /api/v1/auth/2fa/login/`` with a valid TOTP code, which is
    the ONLY path that mints the token for enrolled users.
    """
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def login(self):
        user = self.serializer.validated_data['user']
        if user_requires_2fa(user):
            self.user = self.serializer.validated_data['user']
            self.token = None
            stash_pending_2fa(self.request, user)
            # Enrollment flow (SMSLY_ENFORCE_ADMIN_2FA): an enforced admin
            # with no confirmed device is routed into pending-setup +
            # verify instead of a TOTP challenge they cannot pass.
            self._2fa_enrollment_required = admin_2fa_enrollment_required(user)
            return
        # SECURITY (token rotation): dj_rest_auth reuses the existing DRF
        # token on every login (Token.objects.get_or_create). Delete first
        # so each login mints a FRESH token — a previously stolen token
        # dies at the next successful login. Trade-off: logging in on a
        # second device revokes the first device's token.
        from rest_framework.authtoken.models import Token

        Token.objects.filter(user=user).delete()
        super().login()

    def get_response(self):
        if getattr(self, 'user', None) is not None and getattr(self, 'token', None) is None:
            # 2FA-gated branch from login() above: authenticated
            # credentials, but the token is withheld until TOTP —
            # or, for enforced admins, until enrollment completes.
            enrollment_required = bool(getattr(self, '_2fa_enrollment_required', False))
            return Response(
                {
                    'requires_2fa': True,
                    'enrollment_required': enrollment_required,
                    'detail': (
                        'Two-factor enrollment required. Fetch your '
                        'authenticator secret from '
                        'POST /api/v1/auth/2fa/pending-setup/, add it to '
                        'your authenticator app, then submit the code to '
                        '/api/v1/auth/2fa/login/.'
                        if enrollment_required
                        else 'Two-factor authentication required. '
                             'Submit your authenticator code to '
                             '/api/v1/auth/2fa/login/.'
                    ),
                },
                status=200,
            )
        response = super().get_response()
        # SECURITY: the token rides the HttpOnly cookie. Keeping it in the
        # JSON body lets XSS persist it — only returned when the operator
        # explicitly opts in via SMSLY_TOKEN_IN_BODY=true.
        if (
            response.status_code == 200
            and not getattr(settings, 'SMSLY_TOKEN_IN_BODY', False)
            and isinstance(getattr(response, 'data', None), dict)
        ):
            response.data.pop('key', None)
        # ``self.token`` is populated by ``dj_rest_auth``'s ``login()``
        # and is the canonical DRF Token instance. We only stamp the
        # cookie on a successful login (status 200) — failure responses
        # have no token and would clear any prior session cookie.
        if response.status_code == 200 and getattr(self, 'token', None):
            try:
                set_auth_cookie(response, self.token.key)
            except Exception:
                # Never let a cookie helper crash the login response.
                # Worst case the user can re-authenticate or the frontend
                # falls back to the body-returned token via the legacy
                # ``Authorization: Token`` header.
                pass
        else:
            # Belt-and-braces: a 2FA-pending response must never carry
            # a stale token cookie from a previous session.
            clear_pending_2fa(self.request)
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
    authentication_classes = []
    throttle_classes = [PasswordResetRateThrottle]


class ThrottledRegistrationView(_BaseRegistrationView):
    """``POST /api/v1/auth/registration/`` with bot-account guard.

    5/hour per IP matches the platform's billing-enforced
    account-creation cap. Operators who need bulk accounts use
    the admin CLI.
    """
    authentication_classes = []
    throttle_classes = [RegistrationRateThrottle]

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        token_key = None
        if isinstance(getattr(response, 'data', None), dict):
            token_key = response.data.get('key')
        if not token_key and getattr(self, 'token', None) is not None:
            token_key = getattr(self.token, 'key', None)
        if token_key and response.status_code in (200, 201):
            try:
                set_auth_cookie(response, token_key)
            except Exception:
                pass
            if (
                not getattr(settings, 'SMSLY_TOKEN_IN_BODY', False)
                and isinstance(getattr(response, 'data', None), dict)
            ):
                response.data.pop('key', None)
        return response
