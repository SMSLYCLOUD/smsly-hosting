"""Celery module."""
import os
import logging
from celery import Celery, signals
from kombu import Exchange, Queue
from celery.schedules import crontab

logger = logging.getLogger(__name__)

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
    import apps.addons.tasks  # noqa: F401
    import apps.deployments.tasks  # noqa: F401
    import apps.deployments.tasks_alerts  # noqa: F401
    import apps.deployments.tasks_ai  # noqa: F401
    import apps.deployments.tasks_ecosystem  # noqa: F401
    import apps.deployments.tasks_metrics  # noqa: F401
    import apps.deployments.tasks_election  # noqa: F401
    import apps.deployments.tasks_replication  # noqa: F401
    import apps.deployments.tasks_mesh  # noqa: F401

# =============================================================================
# Beat Schedule — Periodic tasks for metrics, health, autoscaling, cleanup
# =============================================================================
# =============================================================================
# Task Routing — Separate fast, deploy, and default queues
# =============================================================================
# Use separate queues for different task types
app.conf.task_default_queue = 'celery'
app.conf.task_default_exchange = 'celery'
app.conf.task_default_routing_key = 'celery'

app.conf.task_queues = (
    Queue('celery', Exchange('celery'), routing_key='celery'),
    Queue('deploy', Exchange('deploy'), routing_key='deploy'),
    Queue('fast', Exchange('fast'), routing_key='fast'),
)

app.conf.task_routes = {
    'apps.deployments.tasks.smart_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks.resume_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks.auto_promote_task': {'queue': 'deploy'},
    'apps.deployments.tasks.promote_deployment_task': {'queue': 'deploy'},
    'apps.deployments.tasks.provision_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.deprovision_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.backup_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.restore_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks_election.heartbeat_task': {'queue': 'fast'},
    'apps.deployments.services.provisioner.cleanup_stale_server_provisioning': {'queue': 'deploy'},
}

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
    # Leader election heartbeat every 5 seconds
    'cluster-heartbeat-every-5s': {
        'task': 'apps.deployments.tasks_election.heartbeat_task',
        'schedule': 5.0,
    },
    # Replication health check every 30 seconds
    'replication-health-every-30s': {
        'task': 'apps.deployments.tasks_replication.check_replication_health_task',
        'schedule': 30.0,
    },
    # Daily intelligence report at 06:00 UTC
    'daily-intelligence-report': {
        'task': 'apps.intelligence.tasks.daily_intelligence_report_task',
        'schedule': crontab(hour=6, minute=0),
    },
    # Proactive health scan every 5 minutes
    'proactive-health-scan-every-5m': {
        'task': 'apps.intelligence.tasks.proactive_health_scan_task',
        'schedule': 300.0,
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
