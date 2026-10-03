import logging

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.consumers.base import authenticate_ws_token
from apps.deployments.services.edge_tokens import EdgeTokenService

logger = logging.getLogger(__name__)


def _legacy_user_from_auth_header(request):
    raw = str(request.headers.get("Authorization", "") or "").strip()
    if not raw:
        return None
    if raw.lower().startswith(("token ", "bearer ")):
        _scheme, _, key = raw.partition(" ")
    else:
        key = raw
    key = key.strip()
    if not key:
        return None
    try:
        from asgiref.sync import async_to_sync
        return async_to_sync(authenticate_ws_token)(key)
    except Exception:
        return None


class EdgeAuthViewSet(viewsets.ViewSet):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    @action(detail=False, methods=["get"], url_path="auth-verify")
    def auth_verify(self, request):
        raw = str(request.headers.get("Authorization", "") or "").strip()
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
        user = None
        scope = "service"
        if token:
            result = EdgeTokenService.verify(token)
            if result is not None:
                user = result["user"]
                scope = result["scope"]
        if user is None:
            user = _legacy_user_from_auth_header(request)
        if user is None or not getattr(user, "is_active", False):
            return Response({"error": "Unauthorized."}, status=status.HTTP_401_UNAUTHORIZED)
        resp = Response({"ok": True, "user_id": str(user.id), "scope": scope})
        resp["X-User-Id"] = str(user.id)
        resp["X-Edge-Scope"] = scope
        return resp

    @action(detail=False, methods=["post"], url_path="token")
    def mint_token(self, request):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return Response({"error": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)
        scope = str((request.data.get("scope") if isinstance(request.data, dict) else "") or "service")
        try:
            ttl = max(60, min(int((request.data.get("ttl") if isinstance(request.data, dict) else 900) or 900), 86400))
        except (TypeError, ValueError):
            ttl = 900
        try:
            token = EdgeTokenService.mint(user, scope=scope, ttl=ttl)
        except Exception as exc:
            logger.warning("Edge token mint failed: %s", exc)
            return Response({"error": "Token service unavailable."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"token": token, "scope": scope, "expires_in": ttl})
