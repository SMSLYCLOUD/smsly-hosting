"""
Multi-server management views.

CRUD + health check + proxy + auto-provisioning for controlling
remote SMSLY Hosting instances.
"""

import hashlib
import hmac as hmac_mod
import logging
import time
from typing import Any
from urllib.parse import urlparse

import requests
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models_servers import ManagedServer

logger = logging.getLogger(__name__)


def _safe_remote_error_payload(kind: str, reason: str, upstream_status: int | None = None) -> dict[str, Any]:
    """
    Return a non-failing payload for remote convenience endpoints.

    This keeps dashboard pages usable even when a remote server is
    temporarily unreachable or still provisioning.
    """
    payload: dict[str, Any] = {
        "remote_unreachable": True,
        "remote_error": str(reason),
        "kind": kind,
    }
    if upstream_status is not None:
        payload["upstream_status"] = int(upstream_status)

    if kind in {"services", "deployments"}:
        payload["results"] = []
        payload["count"] = 0
    elif kind == "domains":
        payload["domains"] = []
        payload["count"] = 0

    return payload


def _build_remote_headers(server, method="GET", path="/api/v1/services/", body=b"", auth_mode=None):
    """
    Build auth headers for a remote server.
    Strategy: token auth when available, otherwise HMAC V2 signing.
    """
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    token = str(server.api_token or "").strip()
    gateway_secret = str(server.gateway_secret or "").strip()

    def _apply_token_auth():
        if not token:
            return False
        if token.lower().startswith("token ") or token.lower().startswith("bearer "):
            headers["Authorization"] = token
        elif token.startswith("smsly_"):
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Authorization"] = f"Token {token}"
        return True

    def _apply_hmac_auth():
        if not gateway_secret:
            return False
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body if isinstance(body, bytes) else b"").hexdigest()
        payload = f"{method}|{path}|{timestamp}|{body_hash}"
        signature = hmac_mod.new(
            gateway_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Gateway-Signature-V2"] = signature
        headers["X-Request-Timestamp"] = timestamp
        return True

    # Explicit mode for fallback paths.
    if auth_mode == "token":
        _apply_token_auth()
        return headers
    if auth_mode == "hmac":
        _apply_hmac_auth()
        return headers

    # Default mode.
    if _apply_token_auth():
        return headers
    if _apply_hmac_auth():
        return headers

    # No auth available: try anyway (will likely fail)
    return headers


def _iter_remote_auth_modes(server):
    """Return auth modes in fallback order for remote requests."""
    modes = []
    if str(server.api_token or "").strip():
        modes.append("token")
    if str(server.gateway_secret or "").strip():
        modes.append("hmac")
    if not modes:
        modes.append("none")
    return modes


def _normalize_remote_api_path(path_or_url):
    """Convert relative or absolute next links into an API path."""
    value = str(path_or_url or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    if not value.startswith("/"):
        return f"/{value}"
    return value


def _extract_page_results_and_next(payload):
    """Extract list results and pagination next pointer from API payload."""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        results = payload.get("results", [])
        if not isinstance(results, list):
            results = []
        return results, payload.get("next")
    return [], None


def _fetch_remote_json_with_fallback(server, kind, api_path, timeout=15):
    """
    Fetch remote JSON with token -> HMAC fallback.

    This handles cases where one auth method is stale but the other is valid.
    """
    normalized_path = _normalize_remote_api_path(api_path)
    url = f"{server.api_url.rstrip('/')}{normalized_path}"
    modes = _iter_remote_auth_modes(server)
    retryable_statuses = {401, 403, 500, 502, 503}

    last_error = None
    last_status = None

    for idx, mode in enumerate(modes):
        headers = _build_remote_headers(
            server,
            method="GET",
            path=normalized_path,
            auth_mode=None if mode == "none" else mode,
        )
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = str(exc)
            continue

        last_status = resp.status_code
        if resp.status_code >= 400:
            has_more_modes = idx < len(modes) - 1
            if has_more_modes and resp.status_code in retryable_statuses:
                continue
            return None, _safe_remote_error_payload(
                kind,
                f"Remote server returned HTTP {resp.status_code}",
                upstream_status=resp.status_code,
            )

        try:
            return resp.json(), None
        except ValueError:
            has_more_modes = idx < len(modes) - 1
            if has_more_modes:
                continue
            return None, _safe_remote_error_payload(
                kind,
                "Remote server returned non-JSON payload.",
                upstream_status=resp.status_code,
            )

    if last_status is not None:
        return None, _safe_remote_error_payload(
            kind,
            f"Remote server returned HTTP {last_status}",
            upstream_status=last_status,
        )
    return None, _safe_remote_error_payload(kind, last_error or "Remote request failed.")


# --- Serializers -------------------------------------------------------------
class ManagedServerSerializer(serializers.ModelSerializer):
    has_ssh_credentials = serializers.SerializerMethodField()

    def get_has_ssh_credentials(self, obj):
        return bool(str(obj.ssh_password or '').strip() or str(obj.ssh_key or '').strip())

    class Meta:
        model = ManagedServer
        fields = [
            "id", "name", "host", "api_url", "ssh_port",
            "is_primary", "status", "last_health_check",
            "server_version", "services_count", "created_at",
            "provision_status", "role", "wg_address", "has_ssh_credentials",
        ]
        read_only_fields = [
            "id", "status", "last_health_check", "server_version",
            "services_count", "created_at", "provision_status",
            "role", "wg_address", "has_ssh_credentials",
        ]


class ManagedServerCreateSerializer(serializers.ModelSerializer):
    """For 'Connect Existing' mode — user provides api_url + api_token."""
    class Meta:
        model = ManagedServer
        fields = ["name", "host", "api_url", "api_token", "gateway_secret", "ssh_password", "ssh_port", "is_primary"]
        extra_kwargs = {
            "api_token": {"write_only": True, "required": False},
            "gateway_secret": {"write_only": True, "required": False},
            "ssh_password": {"write_only": True, "required": False},
        }

    def to_representation(self, instance):
        # Return the stable read serializer shape after create/update operations.
        return ManagedServerSerializer(instance).data

    def validate_host(self, value):
        """Strip protocol and port from host — should be bare IP or domain."""
        import re
        value = re.sub(r'^https?://', '', value).strip().rstrip('/')
        value = re.sub(r':\d+$', '', value)
        return value

    def validate_api_url(self, value):
        """Ensure api_url has a protocol prefix."""
        value = value.strip().rstrip('/')
        if value and not value.startswith(('http://', 'https://')):
            value = f'https://{value}'
        return value


class ManagedServerProvisionSerializer(serializers.ModelSerializer):
    """For 'Provision New' mode — user provides SSH credentials."""
    ssh_auth_method = serializers.ChoiceField(
        choices=["password", "key"], write_only=True,
    )

    class Meta:
        model = ManagedServer
        fields = [
            "name", "host", "ssh_port", "ssh_user",
            "ssh_password", "ssh_key", "ssh_auth_method",
            "is_primary",
        ]
        extra_kwargs = {
            "ssh_password": {"write_only": True},
            "ssh_key": {"write_only": True},
        }

    def validate(self, data):
        method = data.get("ssh_auth_method", "password")
        if method == "password" and not data.get("ssh_password"):
            raise serializers.ValidationError(
                {"ssh_password": "Password is required for password auth."}
            )
        if method == "key" and not data.get("ssh_key"):
            raise serializers.ValidationError(
                {"ssh_key": "SSH private key is required for key auth."}
            )
        return data


# ─── ViewSet ─────────────────────────────────────────────────────────────────

class ManagedServerViewSet(viewsets.ModelViewSet):
    """CRUD for managed remote servers, plus health check, proxy, and provisioning."""

    queryset = ManagedServer.objects.all()
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(owner=self.request.user)

    def get_serializer_class(self):
        if self.action == "provision_new":
            return ManagedServerProvisionSerializer
        if self.action in ["create", "update", "partial_update"]:
            return ManagedServerCreateSerializer
        return ManagedServerSerializer

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    # ── Provision New Server ─────────────────────────────────────────────

    @action(detail=False, methods=["post"], url_path="provision")
    def provision_new(self, request):
        """
        Create a server record and kick off auto-provisioning via SSH.
        The installer will run on the remote VPS and auto-fill api_url/api_token.
        """
        serializer = ManagedServerProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Remove the non-model field before saving
        validated = serializer.validated_data.copy()
        validated.pop("ssh_auth_method", None)

        server = ManagedServer.objects.create(
            owner=request.user,
            provision_status=ManagedServer.ProvisionStatus.PENDING,
            **validated,
        )

        # Kick off async provisioning
        from .services.provisioner import provision_server
        provision_server.delay(str(server.id))

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="provision-logs")
    def provision_logs(self, request, pk=None):
        """Get the provisioning logs for a server."""
        server = self.get_object()
        return Response({
            "provision_status": server.provision_status,
            "provision_logs": server.provision_logs,
        })

    # ── Health Check ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def health_check(self, request, pk=None):
        """Ping a remote server's API to check if it's online."""
        server = self.get_object()

        if not server.api_url:
            return Response(
                {"error": "Server has no API URL yet (provisioning may still be in progress)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        base = server.api_url.rstrip('/')

        # Step 1: unauthenticated /health check — determines online/offline
        try:
            resp = requests.get(f"{base}/health", timeout=10)
            if resp.status_code < 500:
                server.status = ManagedServer.Status.ONLINE
            else:
                server.status = ManagedServer.Status.OFFLINE
        except requests.RequestException:
            server.status = ManagedServer.Status.OFFLINE

        # Step 2: if online, try authenticated call for service count
        if server.status == ManagedServer.Status.ONLINE:
            api_path = "/api/v1/services/"
            headers = _build_remote_headers(server, method="GET", path=api_path)
            try:
                resp = requests.get(f"{base}{api_path}", headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    services = data.get("results", data) if isinstance(data, dict) else data
                    server.services_count = len(services) if isinstance(services, list) else 0
            except requests.RequestException:
                pass  # Online but can't fetch services — token might be wrong

        server.last_health_check = timezone.now()
        server.save(update_fields=["status", "last_health_check", "services_count"])

        return Response(ManagedServerSerializer(server).data)

    @action(detail=False, methods=["post"])
    def check_all(self, request):
        """Health check all servers at once."""
        servers = self.get_queryset().exclude(api_url="")
        results = []
        for server in servers:
            base = server.api_url.rstrip('/')

            # Step 1: unauthenticated /health check
            try:
                resp = requests.get(f"{base}/health", timeout=10)
                if resp.status_code < 500:
                    server.status = ManagedServer.Status.ONLINE
                else:
                    server.status = ManagedServer.Status.OFFLINE
            except requests.RequestException:
                server.status = ManagedServer.Status.OFFLINE

            # Step 2: if online, try authenticated call for service count
            if server.status == ManagedServer.Status.ONLINE:
                api_path = "/api/v1/services/"
                headers = _build_remote_headers(server, method="GET", path=api_path)
                try:
                    resp = requests.get(f"{base}{api_path}", headers=headers, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        services = data.get("results", data) if isinstance(data, dict) else data
                        server.services_count = len(services) if isinstance(services, list) else 0
                except requests.RequestException:
                    pass

            server.last_health_check = timezone.now()
            server.save(update_fields=["status", "last_health_check", "services_count"])
            results.append(ManagedServerSerializer(server).data)

        return Response({"servers": results})

    # ── Proxy ────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def proxy(self, request, pk=None):
        """
        Forward an API request to a remote server.
        Body: { "method": "GET", "path": "/api/v1/services/", "body": null }
        """
        import posixpath
        import json as json_mod
        from urllib.parse import urlparse

        server = self.get_object()
        method = request.data.get("method", "GET").upper()
        raw_path = str(request.data.get("path", "") or "")
        body = request.data.get("body")

        # Preserve query params while normalizing just the path segment.
        path_part, _, query_part = raw_path.partition("?")
        normalized_path = posixpath.normpath(path_part or "/")
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        path = f"{normalized_path}?{query_part}" if query_part else normalized_path

        # Reject any path containing ".." even after normalization
        if ".." in path:
            return Response(
                {"error": "Directory traversal is not allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Re-verify /api/ prefix after normalization
        if not path.startswith("/api/"):
            return Response(
                {"error": "Only /api/ paths can be proxied."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate only HTTP/HTTPS methods are allowed
        allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        if method not in allowed_methods:
            return Response(
                {"error": f"Method {method} is not allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate the server URL scheme is HTTP/HTTPS (prevent file://, etc.)
        parsed = urlparse(server.api_url)
        if parsed.scheme not in ("http", "https"):
            return Response(
                {"error": "Server API URL must use HTTP or HTTPS."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = f"{server.api_url.rstrip('/')}{path}"
        body_bytes = json_mod.dumps(body, sort_keys=True).encode() if body is not None else b""
        headers = _build_remote_headers(server, method=method, path=path, body=body_bytes)

        try:
            resp = requests.request(
                method, url,
                headers=headers,
                json=body if body else None,
                timeout=30,
            )
            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text[:2000]}

            return Response({
                "status_code": resp.status_code,
                "data": data,
            })
        except requests.RequestException as e:
            return Response(
                {"error": f"Proxy request failed: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    # ── Remote Services (convenience) ────────────────────────────────────

    @action(detail=True, methods=["get"])
    def services(self, request, pk=None):
        """Fetch services from a remote server."""
        server = self.get_object()
        if not server.api_url:
            return Response(_safe_remote_error_payload("services", "Server has no API URL yet."))

        api_path = "/api/v1/services/"
        payload, error_payload = _fetch_remote_json_with_fallback(
            server, "services", api_path, timeout=15
        )
        if error_payload:
            return Response(error_payload)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def deployments(self, request, pk=None):
        """Fetch deployments from a remote server."""
        server = self.get_object()
        if not server.api_url:
            return Response(_safe_remote_error_payload("deployments", "Server has no API URL yet."))

        api_path = "/api/v1/deployments/"
        payload, error_payload = _fetch_remote_json_with_fallback(
            server, "deployments", api_path, timeout=15
        )
        if error_payload:
            return Response(error_payload)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def domains(self, request, pk=None):
        """Aggregate all custom domains across all services on a remote server."""
        server = self.get_object()

        if not server.api_url:
            return Response(_safe_remote_error_payload("domains", "Server has no API URL yet."))

        all_services = []
        seen_paths = set()
        next_path = "/api/v1/services/"
        max_pages = 50

        for _ in range(max_pages):
            normalized_path = _normalize_remote_api_path(next_path)
            if not normalized_path or normalized_path in seen_paths:
                break
            seen_paths.add(normalized_path)

            payload, error_payload = _fetch_remote_json_with_fallback(
                server, "domains", normalized_path, timeout=15
            )
            if error_payload:
                if not all_services:
                    return Response(error_payload)
                break

            services_page, next_link = _extract_page_results_and_next(payload)
            all_services.extend(services_page)
            if not next_link:
                break
            next_path = _normalize_remote_api_path(next_link)

        domains = []
        for svc in all_services:
            svc_id = svc.get("id", "")
            svc_name = svc.get("name", "")
            public_domain = svc.get("public_domain", "")
            custom_domains = svc.get("custom_domains", [])
            if not isinstance(custom_domains, list):
                custom_domains = []
            for domain in custom_domains:
                domains.append({
                    "domain": domain,
                    "service_id": svc_id,
                    "service_name": svc_name,
                    "public_domain": public_domain,
                    "verified": svc.get("domain_verified", False),
                    "verification_token": svc.get("verification_token", ""),
                })

        return Response({"domains": domains, "count": len(domains)})

