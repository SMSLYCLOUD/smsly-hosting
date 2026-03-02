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
