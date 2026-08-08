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
        """Called by WebSocket handler when node pushes telemetry.

        Only writes to Redis — the flush_telemetry_to_db Celery task
        handles the DB write to avoid duplicate/mutex issues.
        """
        cache.set(f"media:telemetry:{node_id}", payload, timeout=30)

    def process_audit_event(self, node_id: str, event: dict):
        """Called by webhook when node reports attestation event."""
        from ..models.attestation import AttestationAuditLog

        # 1. Local cache (authoritative copy lives in SMSLYCLOUD Chain)
        AttestationAuditLog.objects.create(
            server_id=node_id,
            event_type=event["event_type"],
            trust_score=event.get("trust_score"),
            metadata=event.get("metadata", {}),
        )

    def get_all_node_status(self) -> dict:
        """O(1) lookup of all node statuses from Redis.

        Uses SCAN instead of KEYS to avoid blocking the event loop.
        """
        result = {}
        cursor = 0
        while True:
            cursor, keys = cache._client.scan(cursor=cursor, match="media:heartbeat:*", count=100)  # noqa: SLF001
            for key in keys:
                result[key] = cache.get(key)
            if cursor == 0:
                break
        return result

    def _log_recovery(self, node_id: str):
        logger.info("Media node %s recovered", node_id)

    def _queue_db_update(self, node_id: str, payload: dict):
        from ..tasks import process_media_heartbeat
        process_media_heartbeat.delay(node_id, payload)
