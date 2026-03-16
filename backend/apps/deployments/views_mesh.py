"""
WireGuard VPN Mesh views.

API endpoints for managing mesh networks, peers, and
deploying WireGuard configurations across the server fleet.
"""

import logging

from django.db import DatabaseError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models_mesh import MeshNetwork, WireGuardPeer
from .tasks_mesh import deploy_mesh_task
from .services.wireguard_service import WireGuardService

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

    class Meta:
        model = MeshNetwork
        fields = [
            "id", "name", "subnet", "listen_port", "interface_name",
            "is_active", "peers", "peer_count", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_peer_count(self, obj):
        return obj.peers.filter(is_active=True).count()


class MeshNetworkCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MeshNetwork
        fields = ["name", "subnet", "listen_port", "interface_name"]


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

    def list(self, request, *args, **kwargs):
        """
        Return mesh list and fail gracefully when mesh tables are unavailable.
        """
        try:
            return super().list(request, *args, **kwargs)
        except (DatabaseError, Exception) as exc:  # noqa: BLE001
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
            server = ManagedServer.objects.get(id=server_id)
        except ManagedServer.DoesNotExist:
            return Response(
                {"error": "Server not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            peer = WireGuardService.add_peer_to_mesh(mesh, server=server)
            # Deploy updated configs to all peers
            # Deploy via celery to avoid connection reset when interface restarts
            deploy_mesh_task.delay(mesh.id)
            results = {"status": "Deploying in background"}
            return Response({
                "peer": WireGuardPeerSerializer(peer).data,
                "deployment": results,
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
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
            # Deploy via celery to avoid connection reset when interface restarts
            deploy_mesh_task.delay(mesh.id)
            results = {"status": "Deploying in background"}
            return Response(results)
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ── Health Check ─────────────────────────────────────────────────

    @action(detail=True, methods=["get"])
    def health(self, request, pk=None):
        """Check connectivity between all peers in the mesh."""
        mesh = self.get_object()
        results = WireGuardService.check_mesh_health(mesh)
        return Response(results)

    # ── WireGuard Status ─────────────────────────────────────────────

    @action(detail=False, methods=["get"])
    def status(self, request):
        """Get local WireGuard interface status."""
        result = WireGuardService.get_wg_status()
        return Response(result)
