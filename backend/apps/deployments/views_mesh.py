"""
WireGuard VPN Mesh views.

API endpoints for managing mesh networks, peers, and
deploying WireGuard configurations across the server fleet.
"""

import ipaddress
import logging

from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models_mesh import MeshNetwork, WireGuardPeer
from .services.wireguard_service import WireGuardService
from .tasks_mesh import deploy_mesh_task

logger = logging.getLogger(__name__)


# ─── Serializers ─────────────────────────────────────────────────────────────

class WireGuardPeerSerializer(serializers.ModelSerializer):
    server_name = serializers.CharField(
        source="server.name", read_only=True, default="local",
    )
    server_host = serializers.CharField(
        source="server.host", read_only=True, default="",
    )

    class Meta:
        model = WireGuardPeer
        fields = [
            "id", "mesh", "server", "server_name", "server_host",
            "public_key", "wg_address", "endpoint", "allowed_ips",
            "is_active", "is_local", "last_handshake", "latency_ms",
            "created_at",
        ]
        read_only_fields = [
            "id", "public_key", "wg_address", "allowed_ips",
            "last_handshake", "latency_ms", "created_at",
        ]


class MeshNetworkSerializer(serializers.ModelSerializer):
    peers = WireGuardPeerSerializer(many=True, read_only=True)
    peer_count = serializers.SerializerMethodField()
    replication_last_result = serializers.SerializerMethodField()

    class Meta:
        model = MeshNetwork
        fields = [
            "id", "name", "subnet", "listen_port", "interface_name",
            "is_active", "mesh_status", "mesh_last_error",
            "mesh_last_result", "mesh_last_deployed_at",
            "replication_status", "replication_last_error",
            "replication_last_result", "replication_updated_at",
            "peers", "peer_count", "created_at",
        ]
        read_only_fields = [
            "id", "created_at", "mesh_status", "mesh_last_error",
            "mesh_last_result", "mesh_last_deployed_at",
            "replication_status", "replication_last_error",
            "replication_last_result", "replication_updated_at",
        ]

    def get_peer_count(self, obj):
        return obj.peers.filter(is_active=True).count()

    def get_replication_last_result(self, obj):
        """Strip haproxy_stats_password from replication results before API exposure.

        The HAProxy stats password is embedded in the compose config needed for
        deployment but must never be returned to API consumers.
        """
        result = obj.replication_last_result or {}
        if isinstance(result, dict):
            # Remove sensitive keys at the top level and within any haproxy section
            sanitized = {k: v for k, v in result.items() if 'password' not in k.lower()}
            if 'haproxy' in result and isinstance(result['haproxy'], dict):
                sanitized['haproxy'] = {
                    k: v for k, v in result['haproxy'].items()
                    if 'password' not in k.lower()
                }
            return sanitized
        return result


class MeshNetworkCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeshNetwork
        fields = ["name", "subnet", "listen_port", "interface_name"]

    def validate_subnet(self, value):
        try:
            ipaddress.IPv4Network(value, strict=False)
        except ValueError as exc:
            raise serializers.ValidationError("subnet must be a valid IPv4 CIDR.") from exc
        return value

    def validate_interface_name(self, value):
        try:
            return WireGuardService.validate_interface_name(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_listen_port(self, value):
        if value < 1 or value > 65535:
            raise serializers.ValidationError("listen_port must be between 1 and 65535.")
        return value


# ─── ViewSets ────────────────────────────────────────────────────────────────

class MeshNetworkViewSet(viewsets.ModelViewSet):
    """
    CRUD for WireGuard mesh networks.

    Actions:
    - list/create/retrieve/update/delete  — standard CRUD
    - add_peer      — add a server to the mesh
    - remove_peer   — remove a server from the mesh
    - deploy        — deploy WG configs to all peers
    - health        — check connectivity between all peers
    - status        — get local WireGuard interface status
    """

    queryset = MeshNetwork.objects.all().prefetch_related("peers")
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        queryset = self.queryset
        user = self.request.user
        if user and user.is_superuser:
            return queryset
        return queryset.filter(Q(project__owner=user) | Q(project__isnull=True))

    def get_serializer_class(self):
        if self.action in ["create"]:
            return MeshNetworkCreateSerializer
        return MeshNetworkSerializer

    def perform_create(self, serializer):
        mesh = serializer.save()
        # Auto-add the local server as the first peer
        try:
            WireGuardService.add_peer_to_mesh(mesh, server=None, is_local=True)
        except Exception as e:
            logger.error(f"Failed to add local peer: {e}")
            mesh.mesh_status = "FAILED"
            mesh.mesh_last_error = str(e)[:2000]
            mesh.save(update_fields=["mesh_status", "mesh_last_error", "updated_at"])

    def list(self, request, *args, **kwargs):
        """
        Return mesh list and fail gracefully when mesh tables are unavailable.
        """
        try:
            return super().list(request, *args, **kwargs)
        except (DatabaseError, Exception) as exc:
            logger.error("Mesh list unavailable: %s", exc)
            return Response(
                {
                    "results": [],
                    "count": 0,
                    "mesh_available": False,
                    "warning": "Mesh datastore unavailable.",
                }
            )

    # ── Add Peer ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="add-peer")
    def add_peer(self, request, pk=None):
        """Add a managed server to this mesh network."""
        mesh = self.get_object()
        server_id = request.data.get("server_id")

        if not server_id:
            return Response(
                {"error": "server_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.deployments.models_servers import ManagedServer
        try:
            server_queryset = ManagedServer.objects.all()
            if not request.user.is_superuser:
                server_queryset = server_queryset.filter(owner=request.user)
            server = server_queryset.get(id=server_id)
        except ManagedServer.DoesNotExist:
            return Response(
                {"error": "Server not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if mesh.mesh_status == "DEPLOYING":
            return Response(
                {"error": "Mesh deployment already in progress."},
                status=status.HTTP_409_CONFLICT,
            )
        if server.status != ManagedServer.Status.ONLINE:
            return Response(
                {"error": f"Server '{server.name}' is {server.status}; only ONLINE servers can join a mesh."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not (server.ssh_key or server.ssh_password):
            return Response(
                {"error": "Server SSH credentials are required before adding it to a mesh."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            peer = WireGuardService.add_peer_to_mesh(mesh, server=server)
            # Deploy updated configs to all peers
            # Deploy via celery to avoid connection reset when interface restarts
            mesh.mesh_status = "DEPLOYING"
            mesh.mesh_last_error = ""
            mesh.save(update_fields=["mesh_status", "mesh_last_error", "updated_at"])
            deploy_mesh_task.delay(str(mesh.id))
            results = {"status": "Deploying in background"}
            return Response({
                "peer": WireGuardPeerSerializer(peer).data,
                "deployment": results,
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            mesh.mesh_status = "FAILED"
            mesh.mesh_last_error = str(e)[:2000]
            mesh.save(update_fields=["mesh_status", "mesh_last_error", "updated_at"])
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ── Remove Peer ──────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="remove-peer")
    def remove_peer(self, request, pk=None):
        """Remove a peer from this mesh network."""
        mesh = self.get_object()
        peer_id = request.data.get("peer_id")

        if not peer_id:
            return Response(
                {"error": "peer_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            peer = WireGuardPeer.objects.get(id=peer_id, mesh=mesh)
        except WireGuardPeer.DoesNotExist:
            return Response(
                {"error": "Peer not found in this mesh."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if peer.is_local:
            return Response(
                {"error": "Cannot remove the local peer. Delete the mesh instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            WireGuardService.remove_peer_from_mesh(peer)
            return Response({"status": "Peer removed and configs updated."})
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ── Deploy ───────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def deploy(self, request, pk=None):
        """Deploy WireGuard configs to all peers in the mesh."""
        mesh = self.get_object()
        try:
            if mesh.mesh_status == "DEPLOYING":
                return Response(
                    {"error": "Mesh deployment already in progress."},
                    status=status.HTTP_409_CONFLICT,
                )
            if mesh.peers.filter(is_active=True).count() < 2:
                return Response(
                    {"error": "Need at least 2 active peers to deploy a mesh."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Deploy via celery to avoid connection reset when interface restarts
            mesh.mesh_status = "DEPLOYING"
            mesh.mesh_last_error = ""
            mesh.mesh_last_deployed_at = timezone.now()
            mesh.save(update_fields=[
                "mesh_status",
                "mesh_last_error",
                "mesh_last_deployed_at",
                "updated_at",
            ])
            deploy_mesh_task.delay(str(mesh.id))
            results = {"status": "Deploying in background", "mesh_status": mesh.mesh_status}
            return Response(results)
        except Exception as e:
            mesh.mesh_status = "FAILED"
            mesh.mesh_last_error = str(e)[:2000]
            mesh.save(update_fields=["mesh_status", "mesh_last_error", "updated_at"])
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ── Health Check ─────────────────────────────────────────────────

    @action(detail=True, methods=["get"])
    def health(self, request, pk=None):
        """Check connectivity between all peers in the mesh."""
        mesh = self.get_object()
        try:
            results = WireGuardService.check_mesh_health(mesh)
        except Exception as exc:
            logger.exception("Mesh health check failed for %s: %s", mesh.id, exc)
            results = {"error": str(exc)[:2000], "peers": []}
        has_error = bool(results.get("error")) or any(
            str(peer.get("status", "")) != "OK"
            for peer in results.get("peers", [])
        )
        mesh.mesh_last_result = results
        mesh.mesh_last_error = results.get("error", "") if has_error else ""
        mesh.mesh_status = "FAILED" if has_error else "ACTIVE"
        mesh.save(update_fields=[
            "mesh_last_result",
            "mesh_last_error",
            "mesh_status",
            "updated_at",
        ])
        return Response(results)

    # ── WireGuard Status ─────────────────────────────────────────────

    @action(detail=False, methods=["get"])
    def status(self, request):
        """Get local WireGuard interface status."""
        result = WireGuardService.get_wg_status()
        return Response(result)
