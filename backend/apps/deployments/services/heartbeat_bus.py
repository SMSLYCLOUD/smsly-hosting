"""
Cluster heartbeat bus — in-process pub/sub for cluster liveness.

A naive Celery beat schedule that runs a heartbeat task every 5 seconds
multiplies the load by the number of nodes in the cluster (e.g. 1000
nodes = 12k tasks/min). The hot path is now a Redis pub/sub
``PUBLISH`` on a single channel plus a per-peer ``SETEX`` snapshot
keyed by ``peer_id``. Subscribers see the latest heartbeat
instantly without going through the Celery worker pool.

Persistence to the database is decoupled: a separate Celery task
(``persist_heartbeats_task``) runs every 60 seconds, scans the
snapshot keys, and writes a single audit row per peer. This keeps
the DB write rate bounded regardless of cluster size.

The bus is intentionally best-effort and fail-open: a Redis
outage must NOT block the HMAC-authenticated ``heartbeat_receive``
view, so all redis calls swallow ``redis.exceptions.RedisError``
and return ``None``.
"""
import json
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)


_PUBSUB_CHANNEL = 'cluster_heartbeats'
_KEY_PREFIX = 'cluster_heartbeat:'
_SNAPSHOT_TTL_SECONDS = 60

# Module-level cache for the Redis client to avoid creating a new
# connection on every publish/get call.
_redis_client = None


def _redis_url() -> str | None:
    """Resolve the Redis URL for the heartbeat bus.

    Prefers an explicit ``settings.REDIS_URL`` (as recommended in
    the issue spec). Falls back to ``settings.REDIS_CACHE_URL`` (the
    Django cache Redis) so deployments that don't set
    ``REDIS_URL`` still work. Returns ``None`` if neither is
    configured, in which case all publish/get helpers become
    no-ops.
    """
    url = getattr(settings, 'REDIS_URL', None) or getattr(
        settings, 'REDIS_CACHE_URL', None,
    )
    return url


def _get_redis():
    """Return a cached connected redis client, or ``None`` if redis is
    unavailable.  Uses Sentinel when configured, otherwise falls back
    to the standalone URL.  The client is cached at module level to
    avoid creating a new connection on every call.

    Reconnection is triggered by ``RedisError`` in callers — not by
    a per-call ``ping()`` — to avoid unnecessary round-trips.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    url = _redis_url()
    if not url:
        return None
    try:
        import redis
        # Try Sentinel first — when SENTINEL_HOSTS is set, route through
        # Sentinel for automatic master failover.
        from config.redis_sentinel import get_master_connection, SENTINEL_ENABLED
        if SENTINEL_ENABLED:
            conn = get_master_connection(
                password=getattr(settings, 'REDIS_PASSWORD', None),
            )
            if conn is not None:
                _redis_client = conn
                return _redis_client
        _redis_client = redis.from_url(url, decode_responses=True)
        return _redis_client
    except Exception as exc:
        logger.warning("heartbeat_bus: redis unavailable: %s", exc)
        return None


def _invalidate_redis():
    """Discard the cached Redis client so the next call creates a fresh one."""
    global _redis_client
    _redis_client = None


def publish_heartbeat(peer_id, wg_address, status, term=None):
    """Publish a heartbeat to the bus and persist a per-peer
    snapshot with a 60s TTL.

    Returns the payload dict on success, ``None`` on failure.
    Failures are logged at WARNING and never raised — the calling
    view treats a publish failure as a transient Redis outage
    and continues with its normal HMAC-authenticated response.
    """
    import redis  # local import keeps cold-path cheap
    payload = {
        'peer_id': str(peer_id) if peer_id is not None else '',
        'wg_address': wg_address or '',
        'status': status or '',
        'term': term,
        'ts': time.time(),
    }
    body = json.dumps(payload, sort_keys=True)
    r = _get_redis()
    if r is None:
        return None
    try:
        r.publish(_PUBSUB_CHANNEL, body)
        r.setex(
            f"{_KEY_PREFIX}{payload['peer_id']}",
            _SNAPSHOT_TTL_SECONDS,
            body,
        )
    except redis.RedisError as exc:
        logger.warning("heartbeat_bus: publish failed: %s", exc)
        _invalidate_redis()
        return None
    return payload


def get_latest_heartbeats():
    """Return all current heartbeat snapshots keyed by peer_id.

    Returns an empty list if redis is unavailable or no snapshots
    exist. Snapshots older than ``_SNAPSHOT_TTL_SECONDS`` are
    auto-expired by Redis and will not appear here.

    Uses ``SCAN`` instead of ``KEYS`` to avoid blocking the Redis
    event loop under large keyspaces.
    """
    import redis
    r = _get_redis()
    if r is None:
        return []
    try:
        keys = list(r.scan_iter(match=f"{_KEY_PREFIX}*", count=200))
    except redis.RedisError as exc:
        logger.warning("heartbeat_bus: scan_iter() failed: %s", exc)
        return []
    if not keys:
        return []
    try:
        values = r.mget(keys)
    except redis.RedisError as exc:
        logger.warning("heartbeat_bus: mget() failed: %s", exc)
        return []
    out = []
    for body in values:
        if not body:
            continue
        try:
            out.append(json.loads(body))
        except (TypeError, ValueError):
            continue
    return out


def _persist_one_heartbeat(snapshot):
    """Persist a single heartbeat snapshot to the DB. Imported
    lazily so the bus module can be imported without forcing the
    deployments app registry to load.

    Skips the insert (returns ``False``) when the audit row
    cannot be constructed — e.g. ``HeartbeatLog.cluster`` is a
    required FK and no cluster context is available on the
    snapshot. The hot publish path is unaffected; this is a
    best-effort audit drain.
    """
    from apps.deployments.models_election import (
        ClusterState,
        HeartbeatLog,
    )

    wg_address = snapshot.get('wg_address') or ''
    if not wg_address:
        return False
    try:
        from apps.deployments.models_servers import ManagedServer
        server = ManagedServer.objects.filter(
            wg_address=wg_address,
        ).first()
    except Exception:
        server = None
    # Resolve or create a default cluster so the audit row has a
    # valid FK target. The bus does not carry cluster context
    # today, so all drained heartbeats are attributed to a
    # synthetic 'bus' cluster. This keeps the audit log queryable
    # without forcing the publish path to learn about clusters.
    cluster = None
    try:
        cluster, _ = ClusterState.objects.get_or_create(
            mesh=None,
            defaults={'state': 'STABLE', 'term': 0},
        )
    except Exception as exc:
        logger.warning("heartbeat_bus: cluster resolve failed: %s", exc)
        return False
    try:
        HeartbeatLog.objects.create(
            cluster=cluster,
            source_server=None,
            target_server=server,
            term=snapshot.get('term') or 0,
            latency_ms=None,
            success=True,
            error_message=f"bus:{snapshot.get('status', '')}",
        )
        return True
    except Exception as exc:
        logger.warning("heartbeat_bus: persist failed: %s", exc)
        return False


from celery import shared_task


@shared_task
def persist_heartbeats_task():
    """Celery task: drain the bus and write one audit row per
    peer. Runs every 60 seconds via the beat schedule. The hot
    publish path does NOT call this — the bus holds the latest
    snapshot with a 60s TTL, so a 60s drainer is enough to keep
    the audit log current without flooding Celery.
    """
    snapshots = get_latest_heartbeats()
    written = 0
    for snapshot in snapshots:
        if _persist_one_heartbeat(snapshot):
            written += 1
    logger.debug("heartbeat_bus: persisted %d heartbeat(s)", written)
    return written
