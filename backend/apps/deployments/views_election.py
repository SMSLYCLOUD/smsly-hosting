"""
Leader election and cluster management views.

Public endpoints (admin-authenticated):
- ClusterViewSet: CRUD + actions (force-election, status)

Internal endpoints (no auth, WireGuard-only access):
- heartbeat_receive: Accept heartbeats from leader
- vote_request: Handle vote requests from candidates
"""

import logging

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework.response import Response

from .models_election import ClusterState, HeartbeatLog, ElectionVote
from .services.election_service import ElectionService

logger = logging.getLogger(__name__)


# ─── Serializers ─────────────────────────────────────────────────────────────

class ClusterStateSerializer(serializers.ModelSerializer):
    leader_name = serializers.CharField(
        source="leader.name", read_only=True, default="local",
    )
    peer_count = serializers.SerializerMethodField()

    class Meta:
        model = ClusterState
        fields = [
            "id", "mesh", "leader", "leader_name", "leader_wg_address",
            "term", "state", "last_heartbeat",
            "heartbeat_interval_ms", "election_timeout_ms", "min_quorum",
            "peer_count", "created_at",
        ]
        read_only_fields = [
            "id", "leader", "leader_wg_address", "term", "state",
            "last_heartbeat", "created_at",
        ]

    def get_peer_count(self, obj):
        if obj.mesh:
            return obj.mesh.peers.filter(is_active=True).count()
        return 0


class HeartbeatLogSerializer(serializers.ModelSerializer):
    source_name = serializers.CharField(
        source="source_server.name", read_only=True, default="local",
    )
    target_name = serializers.CharField(
        source="target_server.name", read_only=True, default="local",
    )

    class Meta:
        model = HeartbeatLog
        fields = [
            "id", "source_name", "target_name", "term",
            "latency_ms", "success", "error_message", "timestamp",
        ]


# ─── Admin ViewSet ───────────────────────────────────────────────────────────

class ClusterViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only cluster state with admin actions.
    """
    queryset = ClusterState.objects.all()
    serializer_class = ClusterStateSerializer
    permission_classes = [IsAdminUser]

    @action(detail=True, methods=["post"], url_path="force-election")
    def force_election(self, request, pk=None):
        """Force a new leader election for this cluster."""
        cluster = self.get_object()
        from .tasks_election import force_election_task

        mesh_id = str(cluster.mesh.id) if cluster.mesh else None
        force_election_task.delay(mesh_id=mesh_id)

        return Response({
            "status": "Election started",
            "term": cluster.term + 1,
        })

    @action(detail=True, methods=["get"])
    def status(self, request, pk=None):
        """Get detailed cluster status."""
        cluster = self.get_object()
        return Response(ElectionService.get_cluster_status(cluster))

    @action(detail=True, methods=["get"])
    def heartbeats(self, request, pk=None):
        """Get recent heartbeat logs."""
        cluster = self.get_object()
        logs = HeartbeatLog.objects.filter(
            cluster=cluster
        ).order_by("-timestamp")[:50]
        return Response(
            HeartbeatLogSerializer(logs, many=True).data
        )


# ─── Internal Endpoints (WireGuard-only, no auth) ───────────────────────────
# These are called by peer servers over the WireGuard mesh.
# No authentication is needed because the WireGuard tunnel itself is
# the authentication layer (only peers with valid keys can connect).

@api_view(["POST"])
@permission_classes([AllowAny])
def heartbeat_receive(request):
    """
    Receive a heartbeat from the cluster leader.

    Called by peer servers over WireGuard mesh.
    """
    term = request.data.get("term")
    leader_wg_address = request.data.get("leader_wg_address")

    if term is None or not leader_wg_address:
        return Response(
            {"error": "term and leader_wg_address are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get or create cluster for the requesting mesh
    from .models_mesh import MeshNetwork
    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        try:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
            accepted = ElectionService.receive_heartbeat(
                cluster, int(term), leader_wg_address,
            )
            return Response({"accepted": accepted})
        except Exception as e:
            logger.error(f"Heartbeat receive error: {e}")

    return Response(
        {"error": "No active mesh found"},
        status=status.HTTP_404_NOT_FOUND,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def vote_request(request):
    """
    Handle a vote request from a candidate server.

    Called during leader election over WireGuard mesh.
    """
    term = request.data.get("term")
    candidate_wg_address = request.data.get("candidate_wg_address")

    if term is None or not candidate_wg_address:
        return Response(
            {"error": "term and candidate_wg_address are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from .models_mesh import MeshNetwork
    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        try:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
            granted = ElectionService.handle_vote_request(
                cluster, int(term), candidate_wg_address,
            )
            return Response({"vote_granted": granted})
        except Exception as e:
            logger.error(f"Vote request error: {e}")

    return Response(
        {"error": "No active mesh found"},
        status=status.HTTP_404_NOT_FOUND,
    )
