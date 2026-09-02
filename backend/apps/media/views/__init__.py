"""Views for Media Node app."""
import logging

from django.core.cache import cache
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import MediaNodeProfile, MediaParticipant, MediaRoom
from ..models.attestation import AttestationAuditLog, AttestationProfile
from ..serializers import (
    AttestationAuditLogSerializer,
    AttestationProfileSerializer,
    MediaNodeProfileSerializer,
    MediaParticipantSerializer,
    MediaRoomSerializer,
)
from ..services.capacity import MediaCapacityService

logger = logging.getLogger(__name__)


def _verify_webhook_hmac(request) -> bool:
    """Verify HMAC signature from a media node webhook.

    Extracts node_id from the request body, looks up the server's
    gateway_secret, and verifies the HMAC V2 signature.
    """
    from apps.deployments.services.agent_registrar_auth import verify_agent_hmac
    from apps.deployments.models.servers import ManagedServer

    node_id = request.data.get("node_id") if isinstance(request.data, dict) else None
    if not node_id:
        logger.warning("Webhook missing node_id — rejecting")
        return False

    try:
        server = ManagedServer.objects.get(id=node_id)
    except ManagedServer.DoesNotExist:
        logger.warning("Webhook for unknown node %s — rejecting", node_id)
        return False

    return verify_agent_hmac(request, server)


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
        """Restart all services on a media node via the management daemon."""
        from ..tasks import restart_media_node_services_task

        node = self.get_object()
        
        # Offload the synchronous HTTP calls to a background task
        # so we don't block the Django worker thread for 80s.
        restart_media_node_services_task.delay(str(node.server_id))

        return Response({
            "status": "restart_queued",
            "node_id": str(node.server_id),
        }, status=status.HTTP_202_ACCEPTED)


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
    def egress_start(self, request, pk=None, _node_pk=None):
        """Start recording via LiveKit Egress."""
        from ..services.livekit_admin import LiveKitAdminService

        room = self.get_object()
        svc = LiveKitAdminService(room.node)
        result = svc.start_egress(room.room_id)
        if not result:
            return Response(
                {"error": "egress_start_failed", "room_id": room.room_id},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({
            "status": "egress_started",
            "room_id": room.room_id,
            "egress_id": result.get("egress_id", ""),
        })

    @action(detail=True, methods=["post"])
    def egress_stop(self, request, pk=None, _node_pk=None):
        """Stop recording."""
        from ..services.livekit_admin import LiveKitAdminService

        room = self.get_object()
        egress_id = request.data.get("egress_id", "")
        if not egress_id:
            return Response(
                {"error": "egress_id_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        svc = LiveKitAdminService(room.node)
        ok = svc.stop_egress(egress_id)
        return Response({
            "status": "egress_stopped" if ok else "egress_stop_failed",
            "room_id": room.room_id,
        })


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


class MediaNodeRegistrationView(viewsets.ViewSet):
    """Self-registration for media nodes.

    After running ``install-media-node.sh``, the node calls this
    endpoint to register itself with the control plane.  Authenticated
    via HMAC V2 using the shared ``gateway_secret`` — no user session
    required.

    POST /api/v1/media/media-nodes/register/
    {
        "node_id": "<uuid>",
        "host": "198.51.100.10",
        "wg_address": "10.100.0.5",
        "gateway_secret": "<shared secret>",
        "livekit_api_key": "...",
        "livekit_api_secret": "...",
        "capabilities": {
            "max_voice_calls": 5000,
            "max_video_rooms": 100,
            "max_participants": 2000,
            "max_rtp_sessions": 10000
        }
    }
    """

    @action(
        detail=False,
        methods=["post"],
        url_path="register",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def register(self, request):
        """Register a new media node with the control plane."""
        from apps.deployments.models.servers import ManagedServer

        data = request.data if isinstance(request.data, dict) else {}
        node_id = data.get("node_id", "").strip()
        host = data.get("host", "").strip()
        gateway_secret = data.get("gateway_secret", "").strip()

        if not node_id or not host or not gateway_secret:
            return Response(
                {"error": "node_id, host, and gateway_secret are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify HMAC — the node signs with its gateway_secret
        from apps.deployments.services.agent_registrar_auth import (
            compute_agent_hmac,
        )

        signature = str(request.headers.get("X-Gateway-Signature-V2", "") or "").strip()
        timestamp = str(request.headers.get("X-Request-Timestamp", "") or "").strip()
        nonce = str(request.headers.get("X-Request-Nonce", "") or "").strip()

        if not signature or not timestamp or not nonce:
            return Response(
                {"error": "missing HMAC headers"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        import hashlib
        import hmac as hmac_mod
        import time

        try:
            req_ts = int(timestamp)
        except (TypeError, ValueError):
            return Response({"error": "invalid timestamp"}, status=status.HTTP_401_UNAUTHORIZED)

        if abs(int(time.time()) - req_ts) > 60:
            return Response({"error": "timestamp expired"}, status=status.HTTP_401_UNAUTHORIZED)

        body_hash = hashlib.sha256(request.body or b"").hexdigest()
        # NOTE: this path string intentionally differs from the mounted URL
        # (/api/v1/media/media-nodes/register/). Installers sign this legacy
        # path in their HMAC payload — do NOT "fix" it without shipping a
        # matching installer update.
        payload_str = f"POST|/api/v1/media-nodes/register/|{timestamp}|{nonce}|{body_hash}"
        expected = hmac_mod.new(
            gateway_secret.encode(), payload_str.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac_mod.compare_digest(expected, signature):
            logger.warning("Media node registration HMAC mismatch from %s", host)
            return Response({"error": "invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        # Create or update ManagedServer
        server, created = ManagedServer.objects.get_or_create(
            id=node_id,
            defaults={
                "name": f"media-{node_id[:8]}",
                "host": host,
                "wg_address": data.get("wg_address", ""),
                "node_type": "media",
                "owner": None,
                "gateway_secret": gateway_secret,
                "provision_status": ManagedServer.ProvisionStatus.DONE,
                "status": ManagedServer.Status.ONLINE,
            },
        )

        if not created:
            # Update existing node
            server.host = host
            server.wg_address = data.get("wg_address", server.wg_address)
            server.gateway_secret = gateway_secret
            server.status = ManagedServer.Status.ONLINE
            server.provision_status = ManagedServer.ProvisionStatus.DONE
            server.save(update_fields=[
                "host", "wg_address", "gateway_secret",
                "status", "provision_status", "updated_at",
            ])

        # Create or update MediaNodeProfile
        caps = data.get("capabilities", {}) if isinstance(data.get("capabilities"), dict) else {}
        profile, _ = MediaNodeProfile.objects.get_or_create(
            server=server,
            defaults={
                "livekit_api_key": data.get("livekit_api_key", ""),
                "livekit_api_secret": data.get("livekit_api_secret", ""),
                "livekit_host": data.get("livekit_host", "127.0.0.1"),
                "livekit_port": int(data.get("livekit_port", 7880)),
                "max_voice_calls": int(caps.get("max_voice_calls", 5000)),
                "max_video_rooms": int(caps.get("max_video_rooms", 100)),
                "max_participants": int(caps.get("max_participants", 2000)),
                "max_rtp_sessions": int(caps.get("max_rtp_sessions", 10000)),
            },
        )

        # Create AttestationProfile if it doesn't exist
        AttestationProfile.objects.get_or_create(server=server)

        logger.info(
            "Media node registered: %s (%s) — %s",
            server.name, server.id, "created" if created else "updated",
        )

        return Response({
            "status": "registered",
            "server_id": str(server.id),
            "created": created,
        })


class MediaWebhookView(viewsets.ViewSet):
    """Webhooks from media nodes (LiveKit, attestation, etc.)."""

    @action(
        detail=False,
        methods=["post"],
        url_path="livekit",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def livekit_webhook(self, request):
        """Handle LiveKit webhook events (room started, ended, etc.).

        Authenticated via HMAC V2 signature from the media node.
        """
        if not _verify_webhook_hmac(request):
            return Response({"error": "unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

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

    @action(
        detail=False,
        methods=["post"],
        url_path="attestation",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def attestation_webhook(self, request):
        """Handle attestation events from edge nodes.

        Authenticated via HMAC V2 signature from the media node.
        """
        if not _verify_webhook_hmac(request):
            return Response({"error": "unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

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


class MediaNodeInterestView(viewsets.ViewSet):
    """Public-facing lead capture for the enterprise media node workflow.

    The media node installation stack is proprietary; this endpoint only
    records contact details so the sales workflow can follow up. Returns
    the mailto fallback subject/body for the frontend.
    """

    @action(
        detail=False,
        methods=["post"],
        url_path="interest",
    )
    def interest(self, request):
        from ..models import MediaNodeInterest
        from ..serializers import MediaNodeInterestSerializer

        serializer = MediaNodeInterestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        lead = serializer.save()
        logger.info("Media node interest captured: %s (%s)", lead.email, lead.company or lead.name)

        subject = "Media Node (Voice & Video) Access Request"
        body = (
            f"Hi SMSLY team,\n\n"
            f"I'd like to get access to the Media Node workflow.\n\n"
            f"Name: {lead.name}\n"
            f"Company: {lead.company or '-'}\n"
            f"Email: {lead.email}\n"
            f"Target host: {lead.host or '-'}\n"
            f"Notes: {lead.notes or '-'}"
        )
        return Response(
            {
                "status": "recorded",
                "id": str(lead.id),
                "mailto_subject": subject,
                "mailto_body": body,
            },
            status=status.HTTP_201_CREATED,
        )
