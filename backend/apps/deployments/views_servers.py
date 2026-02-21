"""
Multi-server management views.

CRUD + health check + proxy + auto-provisioning for controlling
remote SMSLY Hosting instances.
"""

import hashlib
import hmac as hmac_mod
import logging
import time

import requests
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models_servers import ManagedServer

logger = logging.getLogger(__name__)


def _build_remote_headers(server, method="GET", path="/api/v1/services/", body=b""):
    """
    Build auth headers for a remote server.
    Strategy: Bearer token if available, otherwise HMAC V2 signing.
    """
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    # Prefer Bearer token (DRF auth — skips HMAC on the remote end)
    if server.api_token:
        headers["Authorization"] = f"Bearer {server.api_token}"
        return headers

    # Fallback: HMAC V2 signing (inter-service auth)
    if server.gateway_secret:
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body if isinstance(body, bytes) else b"").hexdigest()
        payload = f"{method}|{path}|{timestamp}|{body_hash}"
        signature = hmac_mod.new(
            server.gateway_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Gateway-Signature-V2"] = signature
        headers["X-Request-Timestamp"] = timestamp
        return headers

    # No auth available — try anyway (will likely fail)
    return headers


# ─── Serializers ─────────────────────────────────────────────────────────────

class ManagedServerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagedServer
        fields = [
            "id", "name", "host", "api_url", "ssh_port",
            "is_primary", "status", "last_health_check",
            "server_version", "services_count", "created_at",
            "provision_status",
        ]
        read_only_fields = [
            "id", "status", "last_health_check", "server_version",
            "services_count", "created_at", "provision_status",
        ]


class ManagedServerCreateSerializer(serializers.ModelSerializer):
    """For 'Connect Existing' mode — user provides api_url + api_token."""
    class Meta:
        model = ManagedServer
        fields = ["name", "host", "api_url", "api_token", "gateway_secret", "ssh_port", "is_primary"]


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

        api_path = "/api/v1/services/"
        url = f"{server.api_url.rstrip('/')}{api_path}"
        headers = _build_remote_headers(server, method="GET", path=api_path)

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                services = data.get("results", data) if isinstance(data, dict) else data
                server.status = ManagedServer.Status.ONLINE
                server.services_count = len(services) if isinstance(services, list) else 0
            else:
                server.status = ManagedServer.Status.OFFLINE
        except requests.RequestException:
            server.status = ManagedServer.Status.OFFLINE

        server.last_health_check = timezone.now()
        server.save(update_fields=["status", "last_health_check", "services_count"])

        return Response(ManagedServerSerializer(server).data)

    @action(detail=False, methods=["post"])
    def check_all(self, request):
        """Health check all servers at once."""
        servers = self.get_queryset().exclude(api_url="")
        results = []
        for server in servers:
            api_path = "/api/v1/services/"
            url = f"{server.api_url.rstrip('/')}{api_path}"
            headers = _build_remote_headers(server, method="GET", path=api_path)
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    services = data.get("results", data) if isinstance(data, dict) else data
                    server.status = ManagedServer.Status.ONLINE
                    server.services_count = len(services) if isinstance(services, list) else 0
                else:
                    server.status = ManagedServer.Status.OFFLINE
            except requests.RequestException:
                server.status = ManagedServer.Status.OFFLINE

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
        from urllib.parse import urlparse

        server = self.get_object()
        method = request.data.get("method", "GET").upper()
        path = request.data.get("path", "")
        body = request.data.get("body")

        # C-2 fix: normalize path to collapse ".." traversal sequences
        path = posixpath.normpath(path)

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
        import json as json_mod
        body_bytes = json_mod.dumps(body).encode() if body else b""
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
        api_path = "/api/v1/services/"
        url = f"{server.api_url.rstrip('/')}{api_path}"
        headers = _build_remote_headers(server, method="GET", path=api_path)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            return Response(resp.json())
        except requests.RequestException as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    @action(detail=True, methods=["get"])
    def deployments(self, request, pk=None):
        """Fetch deployments from a remote server."""
        server = self.get_object()
        api_path = "/api/v1/deployments/"
        url = f"{server.api_url.rstrip('/')}{api_path}"
        headers = _build_remote_headers(server, method="GET", path=api_path)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            return Response(resp.json())
        except requests.RequestException as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    @action(detail=True, methods=["get"])
    def domains(self, request, pk=None):
        """Aggregate all custom domains across all services on a remote server."""
        server = self.get_object()

        if not server.api_url:
            return Response(
                {"error": "Server has no API URL yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_path = "/api/v1/services/"
        url = f"{server.api_url.rstrip('/')}{api_path}"
        headers = _build_remote_headers(server, method="GET", path=api_path)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            services = data.get("results", data) if isinstance(data, dict) else data

            domains = []
            for svc in (services if isinstance(services, list) else []):
                svc_id = svc.get("id", "")
                svc_name = svc.get("name", "")
                public_domain = svc.get("public_domain", "")
                for domain in svc.get("custom_domains", []):
                    domains.append({
                        "domain": domain,
                        "service_id": svc_id,
                        "service_name": svc_name,
                        "public_domain": public_domain,
                        "verified": svc.get("domain_verified", False),
                        "verification_token": svc.get("verification_token", ""),
                    })

            return Response({"domains": domains, "count": len(domains)})
        except requests.RequestException as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
