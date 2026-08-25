"""Custom Prometheus metrics for the SMSLY Hosting platform.

These metrics are exposed via django_prometheus at /metrics and are
also pushed to cAdvisor/long-term Prometheus via the django_prometheus
client integration.
"""
from prometheus_client import Counter, Gauge, Histogram

SERVICE_DEPLOYMENTS_TOTAL = Counter(
    'smsly_deployments_total',
    'Total deployments triggered',
    ['service_id', 'status'],
)

SERVICE_BUILDS_TOTAL = Counter(
    'smsly_builds_total',
    'Total builds executed',
    ['result'],
)

SERVICES_ACTIVE = Gauge(
    'smsly_services_active',
    'Number of active services',
)

DEPLOYMENT_DURATION = Histogram(
    'smsly_deployment_duration_seconds',
    'Deployment duration in seconds',
    buckets=(30, 60, 120, 300, 600, 1200, 3600),
)

ADDON_PROVISION_DURATION = Histogram(
    'smsly_addon_provision_duration_seconds',
    'Time taken to provision an addon',
    ['addon_type'],
    buckets=(5, 15, 30, 60, 120, 300, 600),
)

# ── Backup / Restore Metrics ──────────────────────────────────────────

SMSLY_BACKUP_TOTAL = Counter(
    'smsly_backup_total',
    'Total backups created',
    ['status', 'type'],
)

SMSLY_BACKUP_SIZE_BYTES = Histogram(
    'smsly_backup_size_bytes',
    'Backup size in bytes',
    ['type'],
    buckets=(1e6, 5e6, 1e7, 5e7, 1e8, 5e8, 1e9, 5e9),
)

SMSLY_BACKUP_DURATION_SECONDS = Histogram(
    'smsly_backup_duration_seconds',
    'Backup operation duration in seconds',
    ['type'],
    buckets=(10, 30, 60, 120, 300, 600, 1800, 3600),
)

SMSLY_BACKUP_CLOUD_UPLOAD_FAILURES_TOTAL = Counter(
    'smsly_backup_cloud_upload_failures_total',
    'Failed cloud uploads for backups',
    ['type'],
)

SMSLY_RESTORE_TOTAL = Counter(
    'smsly_restore_total',
    'Total restores triggered',
    ['status', 'type'],
)

# ── Node Liveness ─────────────────────────────────────────────────────
# Mirrors ManagedServer.status so Prometheus can alert on offline nodes
# (NodeOffline rule). Exported from the periodic health/heartbeat tasks.

NODE_HEALTH_STATUS = Gauge(
    'smsly_node_health_status',
    'Managed node liveness (1 = ONLINE, 0 = OFFLINE)',
    ['node'],
)


def export_node_health() -> int:
    """Push current ManagedServer statuses into the node health gauge.

    Called from the heartbeat and server-health beat tasks. Model import
    is deferred to avoid circular imports at settings load time.
    Returns the number of nodes currently ONLINE.
    """
    from apps.deployments.models.core import ManagedServer

    online = 0
    for server in ManagedServer.objects.all().only('name', 'status'):
        is_online = server.status == ManagedServer.Status.ONLINE
        NODE_HEALTH_STATUS.labels(node=server.name).set(1 if is_online else 0)
        if is_online:
            online += 1
    return online
