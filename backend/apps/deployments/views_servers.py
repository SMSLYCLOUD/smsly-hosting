"""
Multi-server management views.

CRUD + health check + proxy for controlling remote SMSLY Hosting instances.
"""

import logging

import requests
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models_servers import ManagedServer

logger = logging.getLogger(__name__)


# ─── Serializer ──────────────────────────────────────────────────────────────

class ManagedServerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagedServer
        fields = [
            "id", "name", "host", "api_url", "ssh_port",
            "is_primary", "status", "last_health_check",
            "server_version", "services_count", "created_at",
        ]
        read_only_fields = ["id", "status", "last_health_check", "server_version", "services_count", "created_at"]


class ManagedServerCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagedServer
        fields = ["name", "host", "api_url", "api_token", "ssh_port", "is_primary"]


# ─── ViewSet ─────────────────────────────────────────────────────────────────

class ManagedServerViewSet(viewsets.ModelViewSet):
    """CRUD for managed remote servers, plus health check and proxy."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ManagedServer.objects.filter(owner=self.request.user)

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return ManagedServerCreateSerializer
        return ManagedServerSerializer

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    # ── Health Check ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def health_check(self, request, pk=None):
        """Ping a remote server's API to check if it's online."""
        server = self.get_object()

        try:
            resp = requests.get(
                f"{server.api_url.rstrip('/')}/api/v1/services/",
                headers={
                    "Authorization": f"Bearer {server.api_token}",
                    "Accept": "application/json",
                },
                timeout=10,
            )
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
        servers = self.get_queryset()
        results = []
        for server in servers:
            try:
                resp = requests.get(
                    f"{server.api_url.rstrip('/')}/api/v1/services/",
                    headers={
                        "Authorization": f"Bearer {server.api_token}",
                        "Accept": "application/json",
                    },
                    timeout=10,
                )
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
        server = self.get_object()
        method = request.data.get("method", "GET").upper()
        path = request.data.get("path", "")
        body = request.data.get("body")

        if not path.startswith("/api/"):
            return Response(
                {"error": "Only /api/ paths can be proxied."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = f"{server.api_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"Bearer {server.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

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
        try:
            resp = requests.get(
                f"{server.api_url.rstrip('/')}/api/v1/services/",
                headers={
                    "Authorization": f"Bearer {server.api_token}",
                    "Accept": "application/json",
                },
                timeout=15,
            )
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
        try:
            resp = requests.get(
                f"{server.api_url.rstrip('/')}/api/v1/deployments/",
                headers={
                    "Authorization": f"Bearer {server.api_token}",
                    "Accept": "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            return Response(resp.json())
        except requests.RequestException as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
