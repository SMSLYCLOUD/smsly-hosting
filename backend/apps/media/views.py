"""Views for Media Node app."""
import logging

from django.core.cache import cache
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import MediaNodeProfile, MediaParticipant, MediaRoom
from .models_attestation import AttestationAuditLog, AttestationProfile
from .serializers import (
    AttestationAuditLogSerializer,
    AttestationProfileSerializer,
    MediaNodeProfileSerializer,
    MediaParticipantSerializer,
    MediaRoomSerializer,
)
from .services.capacity import MediaCapacityService

logger = logging.getLogger(__name__)


class MediaNodeProfileViewSet(viewsets.ModelViewSet):
    """CRUD for media node profiles + live stats + health."""

    queryset = MediaNodeProfile.objects.select_related("server").all()
    serializer_class = MediaNodeProfileSerializer

    @action(detail=True, methods=["get"])
    def health(self, request, pk=None):
        """Real-time health check via management daemon."""
        node = self.get_object()
        cache_key = f"media:heartbeat:{node.server_id}"
        cached = cache.get(cache_key)
        if cached:
            return Response(cached)
        return Response({"status": "no_data", "node_id": str(node.server_id)})

    @action(detail=True, methods=["post"])
    def restart(self, request, pk=None):
        """Restart all services on a media node."""
        node = self.get_object()
        # TODO: SSH or management daemon call
        return Response({"status": "restart_queued", "node_id": str(node.server_id)})


class MediaRoomViewSet(viewsets.ModelViewSet):
    """Rooms nested under media nodes."""

    serializer_class = MediaRoomSerializer

    def get_queryset(self):
        qs = MediaRoom.objects.select_related("node__server").all()
        node_id = self.kwargs.get("node_pk")
        if node_id:
            qs = qs.filter(node_id=node_id)
        return qs

    @action(detail=True, methods=["post"])
    def egress_start(self, request, pk=None, node_pk=None):
        """Start recording via LiveKit Egress."""
        room = self.get_object()
        # TODO: LiveKitAdminService.start_egress(room.room_id)
        return Response({"status": "egress_started", "room_id": room.room_id})

    @action(detail=True, methods=["post"])
    def egress_stop(self, request, pk=None, node_pk=None):
        """Stop recording."""
        room = self.get_object()
        # TODO: LiveKitAdminService.stop_egress(room.room_id)
        return Response({"status": "egress_stopped", "room_id": room.room_id})


class MediaParticipantViewSet(viewsets.ModelViewSet):
    """Participants nested under rooms."""

    serializer_class = MediaParticipantSerializer

    def get_queryset(self):
        qs = MediaParticipant.objects.select_related("room").all()
        room_id = self.kwargs.get("room_pk")
        if room_id:
            qs = qs.filter(room_id=room_id)
        return qs


class AttestationProfileViewSet(viewsets.ModelViewSet):
    """Attestation profiles — read-only from dashboard."""

    queryset = AttestationProfile.objects.select_related("server").all()
    serializer_class = AttestationProfileSerializer
    http_method_names = ["get", "head", "options"]


class AttestationAuditLogViewSet(viewsets.ModelViewSet):
    """Attestation audit events — read-only from dashboard."""

    queryset = AttestationAuditLog.objects.select_related("server").all()
    serializer_class = AttestationAuditLogSerializer
    http_method_names = ["get", "head", "options"]


class MediaCapacityView(viewsets.ViewSet):
    """Global capacity overview and best-node routing."""

    @action(detail=False, methods=["get"])
    def best_node(self, request):
        """Return the best node for a new room/call."""
        room_type = request.query_params.get("room_type", "video")
        node = MediaCapacityService.find_best_node(room_type)
        if not node:
            return Response({"error": "no_nodes_available"}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "server_id": str(node.server_id),
            "host": node.server.host,
            "wg_address": node.server.wg_address,
            "capacity_score": node.capacity_score,
        })


class MediaWebhookView(viewsets.ViewSet):
    """Webhooks from media nodes (LiveKit, attestation, etc.)."""

    @action(detail=False, methods=["post"], url_path="livekit")
    def livekit_webhook(self, request):
        """Handle LiveKit webhook events (room started, ended, etc.)."""
        event = request.data
        event_type = event.get("event", "")
        room_id = event.get("room", {}).get("sid", "")

        if event_type == "room_created":
            logger.info("LiveKit room created: %s", room_id)
        elif event_type == "room_finished":
            logger.info("LiveKit room finished: %s", room_id)
            MediaRoom.objects.filter(room_id=room_id).update(
                status=MediaRoom.Status.ENDED,
                ended_at=timezone.now(),
            )

        return Response({"status": "ok"})

    @action(detail=False, methods=["post"], url_path="attestation")
    def attestation_webhook(self, request):
        """Handle attestation events from edge nodes."""
        node_id = request.data.get("node_id")
        event_type = request.data.get("event_type")
        trust_score = request.data.get("trust_score")
        metadata = request.data.get("metadata", {})

        if not node_id or not event_type:
            return Response({"error": "missing_fields"}, status=status.HTTP_400_BAD_REQUEST)

        AttestationAuditLog.objects.create(
            server_id=node_id,
            event_type=event_type,
            trust_score=trust_score,
            metadata=metadata,
        )

        # Update attestation profile counters
        profile = AttestationProfile.objects.filter(server_id=node_id).first()
        if profile:
            if event_type == "stamp_generated":
                profile.stamps_generated_total += 1
                profile.monotonic_counter = metadata.get("monotonic_counter", profile.monotonic_counter)
                profile.last_attestation_at = timezone.now()
                profile.engine_healthy = True
                profile.save(update_fields=[
                    "stamps_generated_total", "monotonic_counter",
                    "last_attestation_at", "engine_healthy",
                ])
            elif event_type == "tamper_detected":
                profile.tamper_detections_total += 1
                profile.save(update_fields=["tamper_detections_total"])

        return Response({"status": "ok"})
