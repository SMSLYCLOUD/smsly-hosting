import logging

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.consumers.base import authenticate_ws_token
from apps.deployments.services.edge_tokens import EdgeTokenService, _normalize_host

logger = logging.getLogger(__name__)

ALLOWED_EDGE_SCOPES = frozenset({"service", "preview", "metrics"})


def _service_hosts(service) -> set[str]:
    hosts: set[str] = set()
    pub = _normalize_host(getattr(service, "public_domain", None))
    if pub:
        hosts.add(pub)
    for dom in (getattr(service, "custom_domains", None) or []):
        h = _normalize_host(dom)
        if h:
            hosts.add(h)
    for alias in (getattr(service, "host_aliases", None) or []):
        if isinstance(alias, dict):
            h = _normalize_host(alias.get("host"))
        else:
            h = _normalize_host(alias)
        if h:
            hosts.add(h)
    return hosts


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
    permission_classes = [permissions.IsAuthenticated]

    @action(
        detail=False,
        methods=["get"],
        url_path="auth-verify",
        authentication_classes=[],
        permission_classes=[permissions.AllowAny],
    )
    def auth_verify(self, request):
        raw = str(request.headers.get("Authorization", "") or "").strip()
        if len(raw) > 8192:
            return Response({"error": "Unauthorized."}, status=status.HTTP_401_UNAUTHORIZED)
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
        fwd_host = _normalize_host(request.headers.get("X-Forwarded-Host", ""))
        user = None
        scope = "service"
        if token:
            result = EdgeTokenService.verify(token, aud=fwd_host)
            if result is not None:
                user = result["user"]
                scope = result["scope"]
        if user is None:
            user = _legacy_user_from_auth_header(request)
        if user is None or not getattr(user, "is_active", False):
            return Response({"error": "Unauthorized."}, status=status.HTTP_401_UNAUTHORIZED)
        is_privileged = bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
        if scope not in ALLOWED_EDGE_SCOPES and not is_privileged:
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
        data = request.data if hasattr(request.data, "get") else {}
        scope = str(data.get("scope") or "service").strip().lower()
        is_privileged = bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
        if scope not in ALLOWED_EDGE_SCOPES and not is_privileged:
            return Response({"error": "Invalid scope."}, status=status.HTTP_403_FORBIDDEN)
        try:
            ttl = max(60, min(int(data.get("ttl") or 900), 86400))
        except (TypeError, ValueError):
            ttl = 900

        requested_aud = _normalize_host(data.get("aud") or data.get("audience") or data.get("host") or data.get("domain"))
        service_id = str(data.get("service_id") or "").strip()
        bound_aud: str | None = None

        if not (service_id or requested_aud) and scope != "admin":
            return Response(
                {"error": "service_id or aud is required for non-admin edge tokens."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if service_id or requested_aud:
            from django.core.exceptions import ValidationError
            from apps.deployments.models import Service

            if service_id:
                try:
                    svc = Service.objects.filter(id=service_id).first()
                except (ValueError, ValidationError):
                    svc = None
                if svc is None:
                    return Response({"error": "Service not found."}, status=status.HTTP_404_NOT_FOUND)
                if not is_privileged and getattr(svc, "owner_id", None) != user.id:
                    return Response({"error": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
                svc_hosts = _service_hosts(svc)
                if requested_aud:
                    if not is_privileged and requested_aud not in svc_hosts:
                        return Response({"error": "Host does not belong to service."}, status=status.HTTP_403_FORBIDDEN)
                    bound_aud = requested_aud
                else:
                    pub = _normalize_host(getattr(svc, "public_domain", None))
                    bound_aud = pub or (sorted(svc_hosts)[0] if svc_hosts else None)
            elif requested_aud:
                if not is_privileged:
                    owned_hosts: set[str] = set()
                    for svc in Service.objects.filter(owner=user):
                        owned_hosts.update(_service_hosts(svc))
                    if requested_aud not in owned_hosts:
                        return Response({"error": "Host does not belong to user."}, status=status.HTTP_403_FORBIDDEN)
                bound_aud = requested_aud

        if not bound_aud and scope != "admin":
            return Response(
                {"error": "A valid host audience (aud or service domain) is required for non-admin edge tokens."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = EdgeTokenService.mint(user, scope=scope, ttl=ttl, aud=bound_aud)
        except Exception as exc:
            logger.warning("Edge token mint failed: %s", exc)
            return Response({"error": "Token service unavailable."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        payload = {"token": token, "scope": scope, "expires_in": ttl}
        if bound_aud:
            payload["aud"] = bound_aud
        return Response(payload)
