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
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.utils import timezone
from rest_framework.authentication import (
    BaseAuthentication,
    SessionAuthentication,
    TokenAuthentication,
)
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

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

    @staticmethod
    def _enforce_cookie_same_origin(request):
        """Require positive same-origin proof on unsafe cookie-authenticated requests."""
        method = (getattr(request, "method", None) or "").upper()
        if method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return

        meta = request.META
        host = (request.get_host() or "").lower()
        host_hostname = (urlparse(f"//{host}").hostname or "").lower() if host else ""
        origin = str(meta.get("HTTP_ORIGIN") or "").strip()
        sec_fetch_site = str(meta.get("HTTP_SEC_FETCH_SITE") or "").strip().lower()
        referer = str(meta.get("HTTP_REFERER") or "").strip()
        x_requested_with = str(meta.get("HTTP_X_REQUESTED_WITH") or "").strip()

        if sec_fetch_site and sec_fetch_site not in ("same-origin", "none"):
            raise PermissionDenied("Cross-site cookie request rejected.")

        origin_matched = False
        referer_matched = False

        if origin:
            if origin.lower() == "null":
                raise PermissionDenied("Cross-origin cookie request rejected.")
            parsed_origin = urlparse(origin)
            origin_host = (parsed_origin.hostname or "").lower()
            origin_scheme = (parsed_origin.scheme or "").lower()
            if (
                origin_scheme not in ("http", "https")
                or not origin_host
                or not host_hostname
                or origin_host != host_hostname
            ):
                raise PermissionDenied("Cross-origin cookie request rejected.")
            origin_matched = True
        elif referer:
            parsed_ref = urlparse(referer)
            ref_host = (parsed_ref.hostname or "").lower()
            ref_scheme = (parsed_ref.scheme or "").lower()
            if (
                ref_scheme not in ("http", "https")
                or not ref_host
                or not host_hostname
                or ref_host != host_hostname
            ):
                raise PermissionDenied("Cross-origin referer rejected.")
            referer_matched = True

        has_same_origin_proof = (
            origin_matched
            or referer_matched
            or sec_fetch_site == "same-origin"
            or bool(x_requested_with)
        )
        if not has_same_origin_proof:
            raise AuthenticationFailed(
                "Same-origin proof required for unsafe cookie-authenticated request."
            )

    def authenticate(self, request):
        # Honor an existing Authorization header first so CLI/API integrations
        # and any future schemes (JWT, etc.) keep working unchanged.
        meta = request.META
        auth_header = str(meta.get("HTTP_AUTHORIZATION", "")).strip()
        if not auth_header:
            cookie_token = get_cookie_token(request)
            if cookie_token:
                # Enforce Origin / Sec-Fetch-Site / Referer / X-Requested-With
                # checks on unsafe methods when authenticating via cookie so
                # cross-site or subdomain <form> POSTs cannot bypass CSRF.
                self._enforce_cookie_same_origin(request)

                # Reuse the parent class' parsing by faking the header. We
                # avoid mutating request.META in place so the rest of the
                # request lifecycle (downstream middleware, logging) still
                # sees the original (headerless) state.
                request = getattr(request, "_request", request)
                request.META = {**request.META, "HTTP_AUTHORIZATION": f"Token {cookie_token}"}
        return super().authenticate(request)

    def authenticate_header(self, request):
        return "Token"
