"""Custom authentication classes for CloudNeuron API.

SECURITY (Batch G): the legacy ``CsrfExemptSessionAuthentication``
class is no longer registered in ``DEFAULT_AUTHENTICATION_CLASSES``.
A view that needs CSRF-exempt session auth must opt in explicitly
with ``@authentication_classes([..., CsrfExemptSessionAuthentication])``
plus a comment justifying the exemption. Relying on a global
fallback disabled CSRF protection on every session-authenticated
request, which allowed a cross-site forged request to land against
any session-cookie holder (admin, OAuth user, etc.).
"""
from rest_framework.authentication import BaseAuthentication, SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth.hashers import check_password
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
