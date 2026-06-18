"""Celery module."""
import os
import logging

if os.name == "nt":
    # Python 3.14's WMI-backed platform detection can hang on some Windows
    # hosts during Celery import. Force platform.py to use its registry/ver
    # fallback path in local development and test runs.
    try:
        import platform
        platform._wmi = None  # pylint: disable=protected-access
    except Exception:
        pass

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
    import apps.deployments.tasks_code_intelligence  # noqa: F401
    import apps.deployments.tasks_metrics  # noqa: F401
    import apps.deployments.tasks_election  # noqa: F401
    import apps.deployments.tasks_replication  # noqa: F401
    import apps.deployments.tasks_mesh  # noqa: F401
    import apps.deployments.services.self_healing_orchestrator  # noqa: F401

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
app.conf.task_create_missing_queues = True

app.conf.task_routes = {
    'apps.deployments.tasks.smart_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks.resume_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks.provision_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.deprovision_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.backup_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.restore_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks_election.heartbeat_task': {'queue': 'fast'},
    'apps.deployments.services.provisioner.cleanup_stale_server_provisioning': {'queue': 'deploy'},
    'apps.deployments.tasks_ecosystem.ecosystem_scan_task': {'queue': 'deploy'},
    'apps.deployments.tasks_ecosystem.ecosystem_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks_ecosystem.ecosystem_release_wave_task': {'queue': 'fast'},
    'apps.deployments.tasks_safedeploy.create_preview_environment_job': {'queue': 'deploy'},
    'apps.deployments.tasks_safedeploy.create_database_clone_job': {'queue': 'deploy'},
    'apps.deployments.tasks_safedeploy.run_migration_validation_job': {'queue': 'deploy'},
    'apps.deployments.tasks_safedeploy.run_preview_tests_job': {'queue': 'deploy'},
    'apps.deployments.tasks_safedeploy.provision_preview_service_job': {'queue': 'deploy'},
    'apps.deployments.tasks_safedeploy.run_preview_health_check_job': {'queue': 'deploy'},
    'apps.deployments.tasks_safedeploy.expire_stale_previews_job': {'queue': 'fast'},
    'apps.deployments.tasks_safedeploy.destroy_preview_environment_job': {'queue': 'deploy'},
    'apps.deployments.tasks.update_remote_server_task': {'queue': 'deploy'},
    'apps.deployments.tasks.self_heal_remote_deployment': {'queue': 'deploy'},
}

app.conf.beat_schedule = {
    # Collect real Docker stats every 60 seconds
    'collect-metrics-every-60s': {
        'task': 'apps.deployments.tasks_metrics.collect_metrics_task',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Check service health every 30 seconds
    'monitor-health-every-30s': {
        'task': 'apps.deployments.services.health_monitor.monitor_health_task',
        'schedule': 30.0,
        'options': {'expires': 30.0},
    },
    # Check autoscale thresholds every 30 seconds
    'check-autoscale-every-30s': {
        'task': 'apps.deployments.services.autoscaler.check_autoscale_task',
        'schedule': 30.0,
        'options': {'expires': 30.0},
    },
    # Collect stats for inline autoscaler dashboard every 60 seconds
    'autoscaler-collect-stats-every-60s': {
        'task': 'apps.autoscaler.tasks.autoscaler_collect_stats',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Mark interrupted/stale server provisioning runs as failed
    'cleanup-stale-server-provisioning-every-5m': {
        'task': 'apps.deployments.services.provisioner.cleanup_stale_server_provisioning',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Intelligence runtime anomaly scan every 3 minutes
    'detect-runtime-anomalies-every-180s': {
        'task': 'apps.intelligence.tasks.detect_anomalies_task',
        'schedule': 180.0,
        'options': {'expires': 180.0},
    },
    # SSL certificate expiry scan every 6 hours
    'check-ssl-certificates-every-6h': {
        'task': 'apps.cloud.services.ssl_monitor.check_ssl_certificates_task',
        'schedule': 21600.0,
        'options': {'expires': 1800.0},
    },
    # Cleanup Docker build cache daily
    'cleanup-build-cache-daily': {
        'task': 'apps.deployments.tasks_metrics.cleanup_build_cache_task',
        'schedule': 86400.0,  # 24 hours
        'options': {'expires': 86400.0},
    },
    # WireGuard mesh health check every 60 seconds
    'mesh-health-check-every-60s': {
        'task': 'apps.deployments.tasks_mesh.check_mesh_health_task',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Leader election heartbeat every 5 seconds
    'cluster-heartbeat-every-5s': {
        'task': 'apps.deployments.tasks_election.heartbeat_task',
        'schedule': 5.0,
        'options': {'expires': 10.0},
    },
    # Replication health check every 30 seconds
    'replication-health-every-30s': {
        'task': 'apps.deployments.tasks_replication.check_replication_health_task',
        'schedule': 30.0,
        'options': {'expires': 30.0},
    },
    # Daily intelligence report at 06:00 UTC
    'daily-intelligence-report': {
        'task': 'apps.intelligence.tasks.daily_intelligence_report_task',
        'schedule': crontab(hour=6, minute=0),
        'options': {'expires': 3600.0},
    },
    # Scan running containers for errors every 5 minutes
    'scan-running-containers-logs-every-5m': {
        'task': 'apps.deployments.tasks_alerts.scan_running_containers_logs_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Proactive health scan every 5 minutes
    'proactive-health-scan-every-5m': {
        'task': 'apps.intelligence.tasks.proactive_health_scan_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Auto-repair inter-node auth every 5 minutes
    'auto-auth-nodes-every-5m': {
        'task': 'apps.deployments.tasks.auto_authenticate_nodes_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Check health of all managed servers every 5 minutes
    'check-managed-servers-health-every-5m': {
        'task': 'apps.deployments.tasks.check_managed_servers_health_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Node watchdog — checks all remote servers and auto-heals every 5 minutes
    'node-watchdog-every-5m': {
        'task': 'apps.deployments.tasks.node_watchdog_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Auto-scaling analysis — Prometheus + Loki → spawn/destroy replicas
    'auto-scaling-analyze-every-3m': {
        'task': 'apps.deployments.tasks_autoscale.analyze_all_services_task',
        'schedule': 180.0,
        'options': {'expires': 180.0},
    },
    # Run scheduled backups every 15 minutes
    'run-scheduled-backups-every-15m': {
        'task': 'apps.deployments.tasks.run_scheduled_backups_task',
        'schedule': 900.0,
        'options': {'expires': 900.0},
    },
    # Expire stale preview environments hourly
    'expire-stale-previews': {
        'task': 'apps.deployments.tasks_safedeploy.expire_stale_previews_job',
        'schedule': 3600.0,
        'options': {'expires': 3600.0},
    },
    # Push master DB snapshot to all lite agents every 6 hours
    'sync-master-db-to-agents-every-6h': {
        'task': 'apps.deployments.tasks.sync_master_db_to_agents_task',
        'schedule': 21600.0,
        'options': {'expires': 21600.0},
    },
    # Run registry garbage collection every 24 hours
    'registry-gc-every-24h': {
        'task': 'apps.deployments.tasks.registry_garbage_collection_task',
        'schedule': 86400.0,
        'options': {'expires': 86400.0},
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')

# Prevent Celery database connection leaks (especially for PgCat)
from django.db import close_old_connections

@signals.task_prerun.connect
def on_task_prerun(**kwargs):
    close_old_connections()

@signals.task_postrun.connect
def on_task_postrun(**kwargs):
    close_old_connections()
