"""Heartbeat/telemetry processing + Chain forwarding.

smsly-hosting (OSS PaaS) only operates the edge + forwards.
The immutable transparency log and verification authority live in
SMSLYCLOUD (see architecture docs §2.2).
"""
import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


class TelemetryService:
    """Processes incoming heartbeats and telemetry from media nodes."""

    def process_heartbeat(self, node_id: str, payload: dict):
        """Called by webhook when node sends heartbeat."""
        # 1. Update Redis cache (instant)
        cache.set(f"media:heartbeat:{node_id}", payload, timeout=120)

        # 2. Check if node went silent
        was_healthy = cache.get(f"media:status:{node_id}") == "online"
        cache.set(f"media:status:{node_id}", "online", timeout=120)

        if not was_healthy:
            self._log_recovery(node_id)

        # 3. Update DB (batched, async via Celery)
        self._queue_db_update(node_id, payload)

    def process_telemetry(self, node_id: str, payload: dict):
        """Called by WebSocket handler when node pushes telemetry."""
        # 1. Update Redis (instant dashboard)
        cache.set(f"media:telemetry:{node_id}", payload, timeout=30)

        # 2. Update DB fields (async, can lag)
        from ..models import MediaNodeProfile

        MediaNodeProfile.objects.filter(server_id=node_id).update(
            cpu_percent=payload.get("system", {}).get("cpu_percent", 0),
            memory_percent=payload.get("system", {}).get("memory_used_mb", 0)
            / max(payload.get("system", {}).get("memory_total_mb", 1), 1)
            * 100,
            active_calls=payload.get("voice", {}).get("active_calls", 0),
            active_rooms=payload.get("video", {}).get("active_rooms", 0),
            active_participants=payload.get("video", {}).get("total_participants", 0),
            active_rtp_sessions=payload.get("media", {}).get("rtp_sessions", 0),
            capacity_score=payload.get("capacity", {}).get("score", 0),
            last_telemetry_at=timezone.now(),
        )

    def process_audit_event(self, node_id: str, event: dict):
        """Called by webhook when node reports attestation event."""
        from ..models_attestation import AttestationAuditLog

        # 1. Local cache (authoritative copy lives in SMSLYCLOUD Chain)
        AttestationAuditLog.objects.create(
            server_id=node_id,
            event_type=event["event_type"],
            trust_score=event.get("trust_score"),
            metadata=event.get("metadata", {}),
        )

    def get_all_node_status(self) -> dict:
        """O(1) lookup of all node statuses from Redis."""
        keys = cache._client.keys("media:heartbeat:*")  # noqa: SLF001
        return {k: cache.get(k) for k in keys}

    def _log_recovery(self, node_id: str):
        logger.info("Media node %s recovered", node_id)

    def _queue_db_update(self, node_id: str, payload: dict):
        from ..tasks import process_media_heartbeat
        process_media_heartbeat.delay(node_id, payload)
