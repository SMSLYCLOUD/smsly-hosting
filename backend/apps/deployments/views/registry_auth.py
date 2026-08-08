"""
Registry RBAC — Token authentication endpoint for Docker token auth.

Validates DRF auth tokens (from cookie or Authorization header),
checks user permissions for the requested registry scope, and returns a
Docker Registry token signed with REGISTRY_HTTP_SECRET.

Flow:
  1. Docker client hits registry → gets 401 with WWW-Authenticate header
  2. Docker client requests token from this endpoint
  3. Endpoint validates platform auth token → checks permissions → returns registry token
  4. Docker client retries registry request with registry token
"""

import logging
import os
import secrets
import threading
import time
import uuid

import jwt as pyjwt
from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from rest_framework.authtoken.models import Token

logger = logging.getLogger(__name__)

# ── Self-healing signing-key state ──────────────────────────────────
# When REGISTRY_HTTP_SECRET / REGISTRY_TOKEN_SIGNING_KEY are both unset we
# generate an ephemeral ``secrets.token_hex(32)`` key for the lifetime of
# the process so registry pulls do not crash. The key is process-local —
# a restart re-generates and invalidates in-flight tokens (their lifetime
# is 1h so this only affects tokens issued <1h before the restart).
#
# Operators MUST persist the generated key via REGISTRY_HTTP_SECRET once
# self-heal fires; a CRITICAL log line is emitted (once per process) so
# this is hard to miss in alerting.
_registry_secret_cache: str | None = None
_registry_secret_lock = threading.Lock()
_registry_secret_self_heal_logged = False


def _reset_registry_secret_cache() -> None:
    """Test helper: clear the module-level self-heal cache."""
    global _registry_secret_cache, _registry_secret_self_heal_logged
    with _registry_secret_lock:
        _registry_secret_cache = None
        _registry_secret_self_heal_logged = False


def _resolve_registry_secret() -> str:
    """Return the secret used to sign Docker registry tokens.

    Resolution order:
      1. ``REGISTRY_HTTP_SECRET`` env var
      2. ``settings.REGISTRY_HTTP_SECRET``
      3. ``REGISTRY_TOKEN_SIGNING_KEY`` env var
      4. SELF-HEAL: ``secrets.token_hex(32)``, cached for the lifetime of
         the process under a ``threading.Lock``. CRITICAL logged once per
         process so operators know to persist it.
    """
    global _registry_secret_cache, _registry_secret_self_heal_logged

    secret = (
        os.environ.get("REGISTRY_HTTP_SECRET")
        or getattr(settings, "REGISTRY_HTTP_SECRET", "")
        or os.environ.get("REGISTRY_TOKEN_SIGNING_KEY", "")
    )
    if secret:
        return secret

    with _registry_secret_lock:
        if _registry_secret_cache is None:
            _registry_secret_cache = secrets.token_hex(32)
            _registry_secret_self_heal_logged = True
        cached = _registry_secret_cache

    if _registry_secret_self_heal_logged:
        logger.critical(
            "REGISTRY_HTTP_SECRET and REGISTRY_TOKEN_SIGNING_KEY are both "
            "unset — self-healed by generating an ephemeral registry token "
            "signing key for this process. Restart will rotate the key and "
            "invalidate any tokens issued <1h ago. Set REGISTRY_HTTP_SECRET "
            "(or REGISTRY_TOKEN_SIGNING_KEY) in the environment to make this "
            "stable across restarts. lib/update.sh generates one during install."
        )
        # Clear the flag so the CRITICAL line fires once per process, not
        # once per token issuance.
        with _registry_secret_lock:
            _registry_secret_self_heal_logged = False

    return cached


def _get_user_from_drf_token(raw_token: str):
    """Validate a DRF authtoken and return the user."""
    try:
        token = Token.objects.select_related("user").get(key=raw_token)
        if token.user and token.user.is_active:
            return token.user
    except Token.DoesNotExist:
        pass
    return None


def _get_platform_user(request: HttpRequest):
    """Extract user from DRF Token in cookie or Authorization header."""
    # Check cookies first (HttpOnly, not accessible to JS)
    for cookie_name in ("auth_token", "__Host-auth_token", "smsly_token", "__Host-smsly_token"):
        raw_token = request.COOKIES.get(cookie_name)
        if raw_token:
            user = _get_user_from_drf_token(raw_token)
            if user:
                return user

    # Fall back to Authorization header
    auth_header = request.headers.get("Authorization", "")
    for prefix in ("Bearer ", "Token "):
        if auth_header.startswith(prefix):
            raw_token = auth_header[len(prefix):]
            user = _get_user_from_drf_token(raw_token)
            if user:
                return user

    return None


def _check_registry_permission(user, scope: str, actions: list[str]) -> bool:
    """
    Determine if user has permission for the requested registry action.

    Scope format: repository:<repo>:<action>
    Example: repository:smsly/backend:pull

    Uses strict matching against projects the user owns or belongs to.
    Anonymous pull is denied — only authenticated users with project/team
    access can pull images.
    """
    if user is None or not user.is_authenticated:
        return False

    if user.is_superuser or user.is_staff:
        return True

    parts = scope.split(":")
    if len(parts) < 2:
        return False

    resource_type = parts[0]
    repo_name = parts[1]
    req_actions = actions if actions else (parts[2].split(",") if len(parts) > 2 else ["pull"])

    if resource_type != "repository":
        return False

    from django.db.models import Q as _Q

    from apps.teams.models import TeamMember as _TeamMember

    from ..models import Project as _Project
    from ..models.project import ProjectMember as _ProjectMember

    # Build set of project prefixes that this user can access
    # Repo names follow the pattern: smsly/<service_name>
    # or: <project_uuid>/<service_name> for scoped repos
    accessible_prefixes = set()

    # Directly owned projects
    for p in _Project.objects.filter(owner=user).only("id", "name"):
        accessible_prefixes.add(p.name.lower())
        accessible_prefixes.add(str(p.id))

    # Projects via team membership
    team_ids = _TeamMember.objects.filter(
        user=user, is_active=True
    ).values_list("team_id", flat=True)
    for p in _Project.objects.filter(
        _Q(team_id__in=list(team_ids))
    ).only("id", "name"):
        accessible_prefixes.add(p.name.lower())
        accessible_prefixes.add(str(p.id))

    # Direct project membership
    project_ids = _ProjectMember.objects.filter(
        user=user
    ).values_list("project_id", flat=True)
    for p in _Project.objects.filter(id__in=list(project_ids)).only("id", "name"):
        accessible_prefixes.add(p.name.lower())
        accessible_prefixes.add(str(p.id))

    # Platform images (smsly/*) are managed by superusers only.
    # Non-superusers may pull platform images for deployments but must
    # never push.
    if not user.is_superuser and repo_name.startswith("smsly/"):
        return req_actions == ["pull"]

    # Check if the repo name starts with any accessible prefix
    repo_lower = repo_name.lower()
    has_access = any(
        repo_lower == prefix or repo_lower.startswith(prefix + "/")
        for prefix in accessible_prefixes
        if prefix
    )

    if not has_access:
        return False

    # If any action requires push (or wildcard), check ownership
    needs_push = any(act in ("push", "*") for act in req_actions)
    if needs_push:
        owned_prefixes = set()
        for p in _Project.objects.filter(owner=user).only("id", "name"):
            owned_prefixes.add(p.name.lower())
            owned_prefixes.add(str(p.id))
        return any(
            repo_lower == prefix or repo_lower.startswith(prefix + "/")
            for prefix in owned_prefixes
            if prefix
        )

    # If only pull is requested, project access is sufficient
    return has_access


@csrf_exempt
@require_GET
def registry_token(request: HttpRequest) -> JsonResponse:
    """
    Docker Registry token endpoint.

    GET /api/v1/registry/auth/?service=<name>&scope=repository:<repo>:<action>
    """
    service = request.GET.get("service", "container-registry")
    scope = request.GET.get("scope", "")

    user = _get_platform_user(request)

    actions = ["pull"]
    if scope:
        parts = scope.split(":")
        if len(parts) >= 3:
            actions = parts[2].split(",")

    # Self-healing: if no signing key is configured anywhere we generate an
    # ephemeral one for the lifetime of the process (see
    # ``_resolve_registry_secret``). Operators will see a CRITICAL log line
    # and must persist the key via REGISTRY_HTTP_SECRET.
    registry_secret = _resolve_registry_secret()

    if not _check_registry_permission(user, scope, actions):
        return JsonResponse(
            {"errors": [{"code": "UNAUTHORIZED", "message": "insufficient permissions"}]},
            status=401,
        )

    now = int(time.time())
    access = []
    if scope:
        access.append({
            "type": scope.split(":")[0],
            "name": scope.split(":")[1] if ":" in scope else scope,
            "actions": actions,
        })

    token_data = {
        "iss": "smsly-platform",
        "sub": user.username if user else "anonymous",
        "aud": service,
        "exp": now + 3600,
        "nbf": now,
        "iat": now,
        "jti": uuid.uuid4().hex,
        "access": access,
    }

    algorithm = "RS256" if registry_secret.startswith("-----") else "HS256"
    token = pyjwt.encode(token_data, registry_secret, algorithm=algorithm)

    return JsonResponse({
        "token": token,
        "access_token": token,
        "expires_in": 3600,
        "issued_at": now,
    })
