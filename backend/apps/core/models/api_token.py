"""
API Token authentication for CLI and programmatic access.

Supports multiple named tokens per user (unlike DRF's built-in single token).
"""

import hashlib
import hmac
import secrets
import time
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

# ---------------------------------------------------------------------------
# Token scopes
# ---------------------------------------------------------------------------
# Permission scopes for MCP/CLI API tokens. ``read`` covers all read-only
# tools (list/search/get/logs); ``write`` covers mutating tools (env var
# changes, rebuilds, provisioning, deletes). An empty scope list means
# legacy full access (tokens created before scopes existed).
VALID_SCOPES = frozenset({"read", "write"})


def validate_projects(projects) -> list:
    """Normalize an optional project-scope value to a list of UUID strings.

    Each entry must be a valid UUID string (Project PKs). Existence and
    readability are checked at mint time by the caller (it knows the
    user); use ``has_project``/``project_scope`` at call time.
    """
    import uuid as _uuid
    if projects is None:
        return []
    if isinstance(projects, str) or not isinstance(projects, (list, tuple)):
        raise ValueError("projects must be a list of project ID strings.")
    cleaned = []
    for project_id in projects:
        if not isinstance(project_id, str):
            raise ValueError(f"Invalid project ID {project_id!r}: must be a UUID string.")
        try:
            normalized = str(_uuid.UUID(str(project_id).strip()))
        except (ValueError, AttributeError):
            raise ValueError(f"Invalid project ID {project_id!r}: must be a UUID string.")
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def validate_scopes(scopes) -> list:
    """Normalize an optional scopes value to a list of valid scope strings."""
    if scopes is None:
        return []
    if isinstance(scopes, str) or not isinstance(scopes, (list, tuple)):
        raise ValueError("scopes must be a list of scope strings, e.g. ['read'].")
    cleaned = []
    for scope in scopes:
        if not isinstance(scope, str) or scope not in VALID_SCOPES:
            raise ValueError(
                f"Invalid scope {scope!r}. Valid scopes: {sorted(VALID_SCOPES)}."
            )
        if scope not in cleaned:
            cleaned.append(scope)
    return cleaned


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
    scopes = models.JSONField(
        default=list,
        blank=True,
        help_text="Permission scopes, e.g. ['read'] or ['read', 'write']. Empty = full access (legacy).",
    )
    projects = models.JSONField(
        default=list,
        blank=True,
        help_text="Project IDs this token may access. Empty = all owner's projects (legacy).",
    )

    class Meta:
        db_table = 'deployments_apitoken'
        ordering = ["-created_at"]
        verbose_name = "API Token"

    def __str__(self):
        return f"{self.name} ({self.prefix}...) - {self.user}"

    # ---- Factory ----

    @classmethod
    def create_token(cls, user, name: str = "CLI Token", scopes=None, projects=None):
        """
        Generate a new token. Returns (APIToken instance, raw_token).

        The raw token is prefixed with "smsly_" for easy identification.
        ``scopes`` is an optional list like ["read"] or ["read", "write"];
        None/empty means legacy full access. ``projects`` is an optional
        list of project ID strings; None/empty means all owner's projects.
        """
        validated_scopes = validate_scopes(scopes)
        validated_projects = validate_projects(projects)
        raw = f"smsly_{secrets.token_hex(24)}"  # 48 hex chars + prefix = 54 chars
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        prefix = raw[:12]

        instance = cls.objects.create(
            user=user,
            name=name,
            prefix=prefix,
            token_hash=token_hash,
            scopes=validated_scopes,
            projects=validated_projects,
        )
        return instance, raw

    def has_scope(self, scope: str) -> bool:
        """True if this token may perform an action requiring ``scope``."""
        current = getattr(self, "scopes", None) or []
        if not current:
            return True  # legacy full access
        return scope in current

    def project_scope(self) -> list | None:
        """Project IDs this token may access, or None for full access.

        Empty/missing means legacy full access (all owner's projects).
        """
        current = getattr(self, "projects", None) or []
        if not current:
            return None
        return [str(p) for p in current if isinstance(p, str)]

    def has_project(self, project_id) -> bool:
        """True if this token may access ``project_id``."""
        scope = self.project_scope()
        if scope is None:
            return True  # legacy full access
        return str(project_id) in scope

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


class RemoteSyncHMACAuthentication(BaseAuthentication):
    """
    Authenticate inter-node sync requests signed with the remote node's
    GATEWAY_SECRET.

    This is intentionally scoped to X-SMSLY-Remote-Sync requests so a gateway
    secret does not become a general-purpose browser/API credential.
    """

    def authenticate(self, request):
        if request.headers.get("X-SMSLY-Remote-Sync") != "1":
            return None

        signature = str(request.headers.get("X-Gateway-Signature-V2", "")).strip()
        timestamp = str(request.headers.get("X-Request-Timestamp", "")).strip()
        nonce = str(request.headers.get("X-Request-Nonce", "")).strip()
        if not signature and not timestamp and not nonce:
            return None
        if not signature or not timestamp or not nonce:
            raise AuthenticationFailed("Incomplete remote sync signature.")

        try:
            request_ts = int(timestamp)
        except ValueError as exc:
            raise AuthenticationFailed("Invalid remote sync timestamp.") from exc

        if abs(int(time.time()) - request_ts) > 300:
            raise AuthenticationFailed("Remote sync timestamp expired.")

        gateway_secret = str(
            getattr(settings, "GATEWAY_SECRET", "")
        ).strip()
        if not gateway_secret:
            raise AuthenticationFailed("Remote sync gateway secret is not configured.")

        # SECURITY (Batch G): nonce replay protection.
        from django.core.cache import cache
        nonce_key = f"remote_sync_nonce:{nonce}"
        if cache.get(nonce_key):
            raise AuthenticationFailed("Remote sync nonce already used.")
        cache.set(nonce_key, "1", timeout=600)

        body_hash = hashlib.sha256(request.body).hexdigest()
        payload = f"{request.method}|{request.get_full_path()}|{timestamp}|{nonce}|{body_hash}"
        expected = hmac.new(
            gateway_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise AuthenticationFailed("Invalid remote sync signature.")

        User = get_user_model()
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not admin:
            raise AuthenticationFailed("No active admin user is available for remote sync.")

        return (admin, None)

    def authenticate_header(self, request):
        return "RemoteSyncHMAC"
