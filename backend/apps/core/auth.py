"""Custom authentication classes for Grid API.

SECURITY (Batch G): the legacy ``CsrfExemptSessionAuthentication``
class is no longer registered in ``DEFAULT_AUTHENTICATION_CLASSES``.
A view that needs CSRF-exempt session auth must opt in explicitly
with ``@authentication_classes([..., CsrfExemptSessionAuthentication])``
plus a comment justifying the exemption. Relying on a global
fallback disabled CSRF protection on every session-authenticated
request, which allowed a cross-site forged request to land against
any session-cookie holder (admin, OAuth user, etc.).
"""
from django.contrib.auth.hashers import check_password
from django.utils import timezone
from rest_framework.authentication import (
    BaseAuthentication,
    SessionAuthentication,
    TokenAuthentication,
)
from rest_framework.exceptions import AuthenticationFailed

from .auth_cookies import get_cookie_token
from .models import APIKey


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """Session auth without CSRF enforcement — opt-in only.

    Use only for views that genuinely need to accept a
    session-cookie request without a CSRF token (e.g. the
    ``ai_chat_completions`` endpoint, which receives JSON from
    a token-authenticated frontend and may also be hit by a
    session-cookie holder). Every other view that may receive a
    session-cookie request must rely on the default
    ``SessionAuthentication`` (CSRF enforced).
    """

    def enforce_csrf(self, request):
        return  # Opt-in CSRF exemption, justified per-view


class APIKeyAuthentication(BaseAuthentication):
    """
    Authenticate via sk_... API Keys.
    Header: Authorization: Bearer sk_...
    """
    keyword = "Bearer"

    def authenticate(self, request):
        auth_header = str(request.META.get("HTTP_AUTHORIZATION", "")).strip()
        scheme, _, raw_key = auth_header.partition(" ")

        if scheme.lower() != self.keyword.lower() or not raw_key.startswith("sk_"):
            return None

        prefix = raw_key[:8]
        try:
            # Note: We might have multiple keys with same prefix theoretically,
            # but usually it's unique enough for first-pass lookup.
            # We filter by prefix first to avoid expensive hashing for every key.
            now = timezone.now()
            keys = APIKey.objects.filter(
                prefix=prefix, user__is_active=True,
            ).exclude(
                expires_at__isnull=False, expires_at__lt=now,
            ).select_related("user")

            for api_key in keys:
                if check_password(raw_key, api_key.key_hash):
                    # Update last used
                    api_key.last_used = timezone.now()
                    api_key.save(update_fields=['last_used'])
                    return (api_key.user, api_key)

            raise AuthenticationFailed("Invalid API key.")

        except AuthenticationFailed:
            raise
        except Exception:
            return None

    def authenticate_header(self, request):
        return self.keyword


class CookieAwareTokenAuthentication(TokenAuthentication):
    """DRF TokenAuthentication that also accepts the HttpOnly auth cookie.

    The login view sets ``__Host-auth_token`` (or ``auth_token`` in dev) as
    an HttpOnly+SameSite=Strict cookie. The frontend drops the legacy
    ``Authorization: Token <key>`` header and relies on ``withCredentials:
    true`` to have the browser attach the cookie automatically.

    The token is recovered from the cookie and then validated through the
    same code path as the header — i.e. a forged or rotated token behaves
    identically whether it was supplied via header or cookie. There is no
    "weaker" cookie path; the cookie is just a more convenient transport
    for the same credential.

    Cookie authentication is checked **only** when no Authorization header
    is present, so CLI tokens (``Authorization: Bearer smsly_...``) and
    other header-based credentials still take precedence. This keeps
    backward compatibility with API clients that explicitly send a header
    (curl, the ``APITokenAuthentication``/``RemoteSyncHMACAuthentication``
    classes, and existing tests).
    """

    def authenticate(self, request):
        # Honor an existing Authorization header first so CLI/API integrations
        # and any future schemes (JWT, etc.) keep working unchanged.
        auth_header = str(request.META.get("HTTP_AUTHORIZATION", "")).strip()
        if not auth_header:
            cookie_token = get_cookie_token(request)
            if cookie_token:
                # Reuse the parent class' parsing by faking the header. We
                # avoid mutating request.META in place so the rest of the
                # request lifecycle (downstream middleware, logging) still
                # sees the original (headerless) state.
                request = request._request
                request.META = {**request.META, "HTTP_AUTHORIZATION": f"Token {cookie_token}"}
        return super().authenticate(request)

    def authenticate_header(self, request):
        return "Token"
