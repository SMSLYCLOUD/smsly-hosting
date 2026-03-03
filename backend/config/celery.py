"""Celery module."""
import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('smsly_hosting')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Explicitly register tasks defined outside of tasks.py files.
# These imports MUST be deferred until Django apps are fully loaded,
# otherwise models.py triggers AppRegistryNotReady.
@app.on_after_finalize.connect
def register_extra_tasks(sender, **kwargs):  # pylint: disable=unused-argument
    import apps.cloud.services.ssl_monitor  # noqa: F401
    import apps.deployments.services.autoscaler  # noqa: F401
    import apps.deployments.services.health_monitor  # noqa: F401
    import apps.deployments.services.provisioner  # noqa: F401
    import apps.deployments.tasks_alerts  # noqa: F401
    import apps.deployments.tasks_ai  # noqa: F401
    import apps.deployments.tasks_ecosystem  # noqa: F401
    import apps.deployments.tasks_metrics  # noqa: F401

# =============================================================================
# Beat Schedule — Periodic tasks for metrics, health, autoscaling, cleanup
# =============================================================================
app.conf.beat_schedule = {
    # Collect real Docker stats every 60 seconds
    'collect-metrics-every-60s': {
        'task': 'apps.deployments.tasks_metrics.collect_metrics_task',
        'schedule': 60.0,
    },
    # Check service health every 30 seconds
    'monitor-health-every-30s': {
        'task': 'apps.deployments.services.health_monitor.monitor_health_task',
        'schedule': 30.0,
    },
    # Check autoscale thresholds every 30 seconds
    'check-autoscale-every-30s': {
        'task': 'apps.deployments.services.autoscaler.check_autoscale_task',
        'schedule': 30.0,
    },
    # Collect stats for inline autoscaler dashboard every 60 seconds
    'autoscaler-collect-stats-every-60s': {
        'task': 'apps.autoscaler.tasks.autoscaler_collect_stats',
        'schedule': 60.0,
    },
    # Mark interrupted/stale server provisioning runs as failed
    'cleanup-stale-server-provisioning-every-5m': {
        'task': 'apps.deployments.services.provisioner.cleanup_stale_server_provisioning',
        'schedule': 300.0,
    },
    # Intelligence runtime anomaly scan every 3 minutes
    'detect-runtime-anomalies-every-180s': {
        'task': 'apps.intelligence.tasks.detect_anomalies_task',
        'schedule': 180.0,
    },
    # SSL certificate expiry scan every 6 hours
    'check-ssl-certificates-every-6h': {
        'task': 'apps.cloud.services.ssl_monitor.check_ssl_certificates_task',
        'schedule': 21600.0,
    },
    # Cleanup Docker build cache daily
    'cleanup-build-cache-daily': {
        'task': 'apps.deployments.tasks_metrics.cleanup_build_cache_task',
        'schedule': 86400.0,  # 24 hours
    },
    # WireGuard mesh health check every 60 seconds
    'mesh-health-check-every-60s': {
        'task': 'apps.deployments.tasks_mesh.check_mesh_health_task',
        'schedule': 60.0,
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
