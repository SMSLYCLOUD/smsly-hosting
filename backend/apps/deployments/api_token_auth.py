"""
API Token authentication for CLI and programmatic access.

Supports multiple named tokens per user (unlike DRF's built-in single token).
"""

import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class APIToken(models.Model):
    """
    Named API token for CLI / programmatic access.

    The raw token is shown once at creation. We store only a SHA-256 hash,
    plus a short prefix for identification (e.g. "smsly_a3b8...").
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_tokens",
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable label, e.g. 'My Laptop CLI'",
    )
    prefix = models.CharField(
        max_length=12,
        db_index=True,
        help_text="First 8 chars of the token for lookup",
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text="SHA-256 hash of the full token",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API Token"

    def __str__(self):
        return f"{self.name} ({self.prefix}...) - {self.user}"

    # ---- Factory ----

    @classmethod
    def create_token(cls, user, name: str = "CLI Token"):
        """
        Generate a new token. Returns (APIToken instance, raw_token).

        The raw token is prefixed with "smsly_" for easy identification.
        """
        raw = f"smsly_{secrets.token_hex(24)}"  # 48 hex chars + prefix = 54 chars
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        prefix = raw[:12]

        instance = cls.objects.create(
            user=user,
            name=name,
            prefix=prefix,
            token_hash=token_hash,
        )
        return instance, raw

    @classmethod
    def verify(cls, raw_token: str):
        """
        Look up a token by hash. Returns (user, token_instance) or raises.
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        try:
            token = cls.objects.select_related("user").get(
                token_hash=token_hash, is_active=True
            )
        except cls.DoesNotExist:
            raise AuthenticationFailed("Invalid or expired API token.") from None

        token.last_used_at = timezone.now()
        token.save(update_fields=["last_used_at"])
        return token.user, token


# ---------------------------------------------------------------------------
# DRF Authentication Backend
# ---------------------------------------------------------------------------

class APITokenAuthentication(BaseAuthentication):
    """
    Authenticate via ``Authorization: Bearer smsly_...`` header.

    Works alongside DRF's built-in TokenAuthentication and SessionAuthentication.
    """

    keyword = "Bearer"

    def authenticate(self, request):
        auth_header = str(request.META.get("HTTP_AUTHORIZATION", "")).strip()
        scheme, _, raw_token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not raw_token.startswith("smsly_"):
            return None  # Not our token format - let other backends try

        user, token = APIToken.verify(raw_token)

        if not user.is_active:
            raise AuthenticationFailed("User account is disabled.")

        return (user, token)

    def authenticate_header(self, request):
        return self.keyword
