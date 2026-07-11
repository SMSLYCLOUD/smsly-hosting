"""Celery tasks for Media Node app."""
import logging

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(queue="media-telemetry")
def check_stale_media_nodes():
    """Detect nodes silent > 60s — mark degraded."""
    from .models import MediaNodeProfile

    stale_threshold = timezone.now() - timezone.timedelta(seconds=60)
    stale_nodes = MediaNodeProfile.objects.filter(
        last_telemetry_at__lt=stale_threshold,
        last_telemetry_at__isnull=False,
    ).exclude(
        last_telemetry_at=None,
    )

    for node in stale_nodes:
        cache.set(f"media:status:{node.server_id}", "degraded", timeout=120)
        logger.warning("Media node %s stale — no telemetry for >60s", node.server_id)


@shared_task(queue="media-telemetry")
def aggregate_media_capacity():
    """Recompute global capacity from Redis telemetry snapshots."""
    from .models import MediaNodeProfile
    from .services.capacity import MediaCapacityService

    nodes = MediaNodeProfile.objects.select_related("server").filter(
        server__agent_ready=True,
        server__provision_status="DONE",
    )

    for node in nodes:
        score = MediaCapacityService.calculate_score(node)
        node.capacity_score = score
        node.save(update_fields=["capacity_score"])


@shared_task(queue="media-telemetry")
def flush_telemetry_to_db():
    """Batch-write Redis telemetry snapshots to PostgreSQL."""
    from .models import MediaNodeProfile

    keys = cache._client.keys("media:telemetry:*")  # noqa: SLF001
    for key in keys:
        data = cache.get(key)
        if not data:
            continue

        node_id = key.split(":")[-1]
        try:
            MediaNodeProfile.objects.filter(server_id=node_id).update(
                cpu_percent=data.get("system", {}).get("cpu_percent", 0),
                memory_percent=data.get("system", {}).get("memory_used_mb", 0)
                / max(data.get("system", {}).get("memory_total_mb", 1), 1)
                * 100,
                active_calls=data.get("voice", {}).get("active_calls", 0),
                active_rooms=data.get("video", {}).get("active_rooms", 0),
                active_participants=data.get("video", {}).get("total_participants", 0),
                active_rtp_sessions=data.get("media", {}).get("rtp_sessions", 0),
                capacity_score=data.get("capacity", {}).get("score", 0),
                last_telemetry_at=timezone.now(),
            )
        except Exception:
            logger.exception("Failed to flush telemetry for node %s", node_id)


@shared_task(queue="deploy")
def rotate_media_node_keys():
    """Trigger key rotation on all media nodes."""
    # TODO: SSH or management daemon call to rotate LiveKit/TURN keys
    logger.info("Key rotation triggered for all media nodes")


@shared_task(queue="media-audit")
def verify_federation_chains():
    """Verify cross-verifier trust chains (hourly)."""
    # TODO: Forward to SMSLYCLOUD Chain verification endpoint
    logger.info("Federation chain verification triggered")


@shared_task(queue="media-telemetry")
def process_media_heartbeat(node_id: str, payload: dict):
    """Process incoming heartbeat from a media node."""
    from .models import MediaNodeProfile

    # Update Redis cache
    cache.set(f"media:heartbeat:{node_id}", payload, timeout=120)

    # Check if node recovered from degraded
    was_degraded = cache.get(f"media:status:{node_id}") == "degraded"
    cache.set(f"media:status:{node_id}", "online", timeout=120)

    if was_degraded:
        logger.info("Media node %s recovered", node_id)

    # Update DB (async via Celery)
    try:
        MediaNodeProfile.objects.filter(server_id=node_id).update(
            active_calls=payload.get("capacity", {}).get("active_calls", 0),
            active_rooms=payload.get("capacity", {}).get("active_rooms", 0),
            active_participants=payload.get("capacity", {}).get("active_participants", 0),
            capacity_score=payload.get("capacity", {}).get("score", 0),
            last_telemetry_at=timezone.now(),
        )
    except Exception:
        logger.exception("Failed to process heartbeat for node %s", node_id)
