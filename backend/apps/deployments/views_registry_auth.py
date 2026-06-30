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

import hashlib
import os
import time
import uuid

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

import jwt as pyjwt
from rest_framework.authtoken.models import Token


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
    requested_action = parts[2] if len(parts) > 2 else "pull"

    if resource_type != "repository":
        return False

    # Everyone authenticated can pull
    if requested_action == "pull":
        return True

    # Push requires ownership
    if requested_action in ("push", "*"):
        if hasattr(user, "owned_projects"):
            for project in user.owned_projects.all():
                if project.name.lower() in repo_name.lower():
                    return True

        if hasattr(user, "team_memberships"):
            for membership in user.team_memberships.all():
                if membership.role in ("ADMIN", "MEMBER", "OWNER"):
                    return True

    return False


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

    if not _check_registry_permission(user, scope, actions):
        return JsonResponse(
            {"errors": [{"code": "UNAUTHORIZED", "message": "insufficient permissions"}]},
            status=401,
        )

    registry_secret = (
        os.environ.get("REGISTRY_HTTP_SECRET")
        or getattr(settings, "REGISTRY_HTTP_SECRET", "")
        or os.environ.get("REGISTRY_TOKEN_SIGNING_KEY", "")
    )

    if not registry_secret:
        registry_secret = hashlib.sha256(
            (getattr(settings, "SECRET_KEY", "change-me") + "-registry").encode()
        ).hexdigest()

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
