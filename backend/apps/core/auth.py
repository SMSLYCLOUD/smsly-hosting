"""Custom authentication classes for CloudNeuron API."""

from rest_framework.authentication import SessionAuthentication


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """
    Session authentication without CSRF enforcement.

    DRF's default SessionAuthentication enforces CSRF on unsafe methods (POST,
    PUT, DELETE, PATCH). This causes 403 errors when:

    1. A user has a session cookie (from OAuth login)
    2. The frontend sends Authorization: Token xxx
    3. The token is stale/invalid (doesn't match DB)
    4. DRF falls through to SessionAuthentication
    5. CSRF check fails → 403

    Since our API primarily uses token auth and the session is only a fallback,
    we skip CSRF enforcement for API requests. The API is already protected by
    token auth and the SecurityMiddleware's HMAC verification.
    """

    def enforce_csrf(self, request):
        """Skip CSRF check for API requests."""
        return  # No-op — CSRF not needed for token-first API

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth.hashers import check_password
from .models import APIKey

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
            keys = APIKey.objects.filter(prefix=prefix, user__is_active=True).select_related("user")
            
            for api_key in keys:
                if check_password(raw_key, api_key.key_hash):
                    # Update last used
                    from django.utils import timezone
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
