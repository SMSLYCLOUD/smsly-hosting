"""
Leader election and cluster management views.

Public endpoints (admin-authenticated):
- ClusterViewSet: CRUD + actions (force-election, status)

Internal endpoints (HMAC-authenticated, WireGuard mesh):
- heartbeat_receive: Accept heartbeats from leader
- vote_request: Handle vote requests from candidates
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import logging
import time

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.deployments.models.election import ClusterState, HeartbeatLog
from apps.deployments.services.election_service import ElectionService

logger = logging.getLogger(__name__)

# SEC-ZT-001: HMAC authentication for election protocol.
# Election heartbeat and vote endpoints require HMAC V2 signatures.
# The HMAC secret is the sender's gateway_secret, verified against
# the corresponding ManagedServer record by wg_address.
HMAC_TIMEOUT_SECONDS = 300  # 5-minute timestamp window for replay protection


def _verify_election_hmac(request) -> tuple[bool, str]:
    """
    Verify HMAC V2 signature on election protocol messages.

    Validates:
    1. X-Request-Timestamp is within HMAC_TIMEOUT_SECONDS of now
    2. X-Election-Signature matches HMAC-SHA256 of body using the
       sender's gateway_secret

    Returns (is_valid, error_reason).
    """
    from apps.deployments.models.mesh import WireGuardPeer

    signature = request.headers.get("X-Election-Signature", "")
    timestamp_str = request.headers.get("X-Request-Timestamp", "")
    sender_wg = request.data.get("sender_wg_address", "")

    if not signature or not timestamp_str or not sender_wg:
        return False, "Missing required HMAC headers"

    try:
        timestamp = int(timestamp_str)
    except (ValueError, TypeError):
        return False, "Invalid X-Request-Timestamp"

    now = int(time.time())
    if abs(now - timestamp) > HMAC_TIMEOUT_SECONDS:
        return False, f"Timestamp outside {HMAC_TIMEOUT_SECONDS}s window"

    # Find the peer by wg_address to get their gateway_secret
    try:
        peer = WireGuardPeer.objects.select_related("server").filter(
            wg_address=sender_wg, is_active=True,
        ).first()
    except Exception:
        return False, "Database error resolving peer"

    if not peer or not peer.server:
        return False, "Unknown peer wg_address"

    gateway_secret = str(getattr(peer.server, "gateway_secret", "") or "").strip()
    if not gateway_secret:
        return False, "No gateway_secret configured for peer"

    body_bytes = request.body or b""
    payload = f"{sender_wg}|{timestamp_str}|{hashlib.sha256(body_bytes).hexdigest()}"
    expected = hmac_mod.new(
        gateway_secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac_mod.compare_digest(signature, expected):
        return False, "HMAC signature mismatch"

    return True, ""


def _election_hmac_required(view_func):
    """Decorator that enforces HMAC auth on election endpoints."""
    def _wrapped(request, *args, **kwargs):
        is_valid, error = _verify_election_hmac(request)
        if not is_valid:
            logger.warning("Election HMAC rejected: %s", error)
            return Response(
                {"error": f"Election HMAC rejected: {error}"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return view_func(request, *args, **kwargs)
    return _wrapped


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
        from apps.deployments.tasks.infra.tasks_election import force_election_task

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


# ─── Internal Endpoints (HMAC-authenticated, WireGuard mesh) ────────────────
# SEC-ZT-001: Election protocol messages require HMAC V2 signature verification.
# The HMAC secret is the sender's per-node gateway_secret, verified against
# the WireGuardPeer's ManagedServer record.

@api_view(["POST"])
@_election_hmac_required
def heartbeat_receive(request) -> Response:
    """
    Receive a heartbeat from the cluster leader.

    Authenticated via HMAC V2 using the sender's per-node gateway_secret.
    Called by peer servers over WireGuard mesh.

    SECURITY: returns a constant ``{"accepted": True}`` on every HMAC-
    authenticated request. Distinguishing 'no mesh configured' from
    'heartbeat accepted' would let a network-adjacent attacker (one
    that has compromised any single peer's gateway_secret, or is the
    peer itself) enumerate whether the platform has any other peer
    configured by toggling MeshNetwork.is_active and reading the
    response shape. The legitimate leader does not need this feedback
    — cluster liveness is determined by timeout, not by the body of
    the heartbeat ack.
    """
    term = request.data.get("term")
    leader_wg_address = request.data.get("leader_wg_address")

    if term is None or not leader_wg_address:
        return Response(
            {"error": "term and leader_wg_address are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from apps.deployments.models.mesh import MeshNetwork
    mesh_qs = (
        MeshNetwork.objects
        .filter(is_active=True, peers__server__wg_address=leader_wg_address)
        .distinct()
    )
    mesh = None
    if mesh_qs.count() == 1:
        mesh = mesh_qs.first()
    elif mesh_qs.count() > 1:
        logger.warning(
            "Heartbeat received from peer wg_address=%s matches %d active "
            "meshes via server; rejecting as ambiguous.",
            leader_wg_address, mesh_qs.count(),
        )
        return Response({"accepted": True})
    if mesh is None:
        peer_qs = (
            MeshNetwork.objects
            .filter(is_active=True, peers__wg_address=leader_wg_address)
            .distinct()
        )
        if peer_qs.count() == 1:
            mesh = peer_qs.first()
        elif peer_qs.count() > 1:
            logger.warning(
                "Heartbeat received from peer wg_address=%s matches %d active "
                "meshes via peer; rejecting as ambiguous.",
                leader_wg_address, peer_qs.count(),
            )
            return Response({"accepted": True})
    if mesh is None:
        logger.warning(
            "Heartbeat received from unknown peer wg_address=%s; "
            "no active mesh contains this peer.",
            leader_wg_address,
        )
    else:
        try:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
            ElectionService.receive_heartbeat(
                cluster, int(term), leader_wg_address,
            )
            from apps.deployments.services.heartbeat_bus import publish_heartbeat
            publish_heartbeat(
                peer_id=leader_wg_address,
                wg_address=leader_wg_address,
                status="alive",
                term=int(term),
            )
        except Exception as e:
            logger.error(f"Heartbeat receive error: {e}")

    return Response({"accepted": True})


@api_view(["POST"])
@_election_hmac_required
def vote_request(request) -> Response:
    """
    Handle a vote request from a candidate server.

    Authenticated via HMAC V2 using the sender's per-node gateway_secret.
    Called during leader election over WireGuard mesh.
    """
    term = request.data.get("term")
    candidate_wg_address = request.data.get("candidate_wg_address")

    if term is None or not candidate_wg_address:
        return Response(
            {"error": "term and candidate_wg_address are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from apps.deployments.models.mesh import MeshNetwork
    mesh_qs = (
        MeshNetwork.objects
        .filter(is_active=True, peers__server__wg_address=candidate_wg_address)
        .distinct()
    )
    mesh = None
    if mesh_qs.count() == 1:
        mesh = mesh_qs.first()
    elif mesh_qs.count() > 1:
        logger.warning(
            "Vote request from candidate wg_address=%s matches %d active "
            "meshes via server; rejecting as ambiguous.",
            candidate_wg_address, mesh_qs.count(),
        )
        return Response(
            {"error": "Ambiguous: candidate belongs to multiple active meshes"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if mesh is None:
        peer_qs = (
            MeshNetwork.objects
            .filter(is_active=True, peers__wg_address=candidate_wg_address)
            .distinct()
        )
        if peer_qs.count() == 1:
            mesh = peer_qs.first()
        elif peer_qs.count() > 1:
            logger.warning(
                "Vote request from candidate wg_address=%s matches %d active "
                "meshes via peer; rejecting as ambiguous.",
                candidate_wg_address, peer_qs.count(),
            )
            return Response(
                {"error": "Ambiguous: candidate belongs to multiple active meshes"},
                status=status.HTTP_404_NOT_FOUND,
            )
    if mesh is None:
        return Response(
            {"error": "No active mesh found for this peer"},
            status=status.HTTP_404_NOT_FOUND,
        )
    try:
        cluster = ElectionService.get_or_create_cluster(mesh=mesh)
        granted = ElectionService.handle_vote_request(
            cluster, int(term), candidate_wg_address,
        )
        return Response({"vote_granted": granted})
    except Exception as e:
        logger.error(f"Vote request error: {e}")
        return Response(
            {"error": "Vote processing failed"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
