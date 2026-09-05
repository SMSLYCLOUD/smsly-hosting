"""Celery module."""
import logging
import os

if os.name == "nt":
    # Python 3.14's WMI-backed platform detection can hang on some Windows
    # hosts during Celery import. Force platform.py to use its registry/ver
    # fallback path in local development and test runs.
    try:
        import platform
        # mypy doesn't see platform._wmi (implementation detail of stdlib on Windows).
        # setattr with a constant string is intentional here: we are patching a
        # private stdlib implementation detail on Python 3.14. A typo would
        # silently miss the workaround, but using setattr keeps the same line
        # structure if the attribute name ever changes upstream.
        setattr(platform, "_wmi", None)  # type: ignore[attr-defined]  # noqa: B010
    except Exception:
        pass

from celery import Celery, signals
from celery.schedules import crontab
from kombu import Exchange, Queue

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
# Celery's autodiscover_tasks() only picks up `tasks.py` — it misses
# the per-feature `tasks_*.py` modules (tasks_templates, tasks_ecosystem,
# tasks_safedeploy, etc.). Without these imports, the worker raises
# "Received unregistered task of type …" when the view queues the
# task, the task never runs, and the user sees a Service stuck on
# "Ready to Deploy" because the background orchestration never started.
# These imports MUST be deferred until Django apps are fully loaded,
# otherwise models.py triggers AppRegistryNotReady.
@app.on_after_finalize.connect
def register_extra_tasks(sender=None, **kwargs):  # pylint: disable=unused-argument
    # Importing the module is enough — the @shared_task decorator
    # registers the task with the worker on import.
    # pylint: disable=import-outside-toplevel
    import apps.deployments.services.auto_rollback
    import apps.deployments.services.autoscaler
    import apps.core.services.health_monitor
    import apps.deployments.services.provisioner
    # -- Tasks defined outside of tasks.py / tasks/__init__.py
    #    (Celery autodiscover_tasks only finds {app}.tasks modules)
    import apps.deployments.services.heartbeat_bus  # noqa: F401
    import apps.cloud.services.ssl_monitor  # noqa: F401
    import apps.intelligence.jules_fix.jules_fix  # noqa: F401
    import apps.cloud.views.code_analysis  # noqa: F401
    import apps.autoscaler.services.legacy_autoscaler  # noqa: F401
    import apps.autoscaler.services.tasks_autoscale  # noqa: F401
    import apps.deployments.tasks.deploy.promote  # noqa: F401  # auto_promote_staged_deployments
    import apps.deployments.services.redis_failover_recovery  # noqa: F401
    import apps.addons.tasks.ha_watchdog  # noqa: F401  # check_addon_ha_task
    # -- Tasks in subpackages not auto-discovered (not {app}.tasks) --
    import apps.deployments.tasks.ai.tasks_ai  # noqa: F401  # analyze_failure_task
    import apps.deployments.tasks.ai.tasks_code_intelligence  # noqa: F401  # deep_scan_and_verify_task
    import apps.deployments.tasks.infra.tasks_health  # noqa: F401  # check_agent_heartbeats_task
    import apps.deployments.tasks.infra.tasks_maintenance  # noqa: F401  # run_maintenance, registry_gc, reconcile_network_isolation_task
    import apps.deployments.tasks.edge_shield_watchdog  # noqa: F401  # BGP/DNS hijack symptom detection
    import apps.deployments.tasks.recover_stale_ecosystem_plans  # noqa: F401  # ghost-plan unblocker (429 lockout fix)
    import apps.deployments.tasks.recover_stalled_deployments  # noqa: F401  # ghost-worker deployment sweeper
    import apps.deployments.tasks.recover_stale_transfers  # noqa: F401  # ghost-transfer unblocker (409 lockout fix)
    import apps.deployments.tasks.build_recovery  # noqa: F401  # corrupt Docker state + pending migration auto-recovery
    import apps.deployments.tasks.service_ha  # noqa: F401  # Service HA pass (local + remote failover)
    import apps.deployments.tasks.infra.tasks_container_hygiene  # noqa: F401  # restart-loop watchdog, orphan addon GC
    import apps.deployments.tasks.tasks_network  # noqa: F401  # scoped Docker network cleanup
    import apps.deployments.tasks.scheduling.tasks_cron  # noqa: F401  # check_cron_jobs, trigger_cron_job
    import apps.deployments.tasks_spiffe  # noqa: F401  # sync_spiffe_entries_task
    import apps.mtls.tasks  # noqa: F401  # inject_mtls_task
    import apps.domains.tasks.reverify  # noqa: F401  # reverify_custom_domains_task (anti-hijack demotion)
    # -- apps.core.tasks is an empty package (only submodules hold tasks),
    #    so autodiscovery has nothing to import. Load them explicitly. --
    import apps.core.tasks.metrics  # noqa: F401  # collect_metrics_task, cleanup_build_cache_task
    import apps.core.tasks.traffic  # noqa: F401  # collect_traefik_logs, resolve_traffic_geolocations
    import apps.core.tasks.alerts  # noqa: F401  # scan_running_containers_logs_task
    # -- All other task modules are auto-discovered via autodiscover_tasks()
    #    which imports {app}.tasks for every INSTALLED_APPS entry. App-level
    #    tasks.py or tasks/__init__.py re-exports from subpackages.

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
    Queue('media-telemetry', Exchange('media-telemetry'), routing_key='media-telemetry'),
    Queue('media-audit', Exchange('media-audit'), routing_key='media-audit'),
)
app.conf.task_create_missing_queues = True

app.conf.task_routes = {
    'apps.deployments.tasks.smart_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks.resume_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks_election.heartbeat_task': {'queue': 'fast'},
    'apps.deployments.services.provisioner.cleanup_stale_server_provisioning': {'queue': 'deploy'},
    'apps.deployments.tasks.cleanup_orphaned_containers_task': {'queue': 'deploy'},
    'apps.deployments.tasks.edge_shield_watchdog': {'queue': 'fast'},
    'apps.deployments.tasks.recover_stale_ecosystem_plans': {'queue': 'fast'},
    'apps.deployments.tasks.recover_stalled_deployments': {'queue': 'fast'},
    'apps.deployments.tasks.apply_service_resource_limits': {'queue': 'fast'},
    'apps.deployments.tasks.recover_stale_transfers': {'queue': 'fast'},
    'apps.deployments.tasks.recover_corrupt_docker_state': {'queue': 'deploy'},
    'apps.deployments.tasks.ensure_migrations_applied': {'queue': 'deploy'},
    'apps.deployments.tasks.service_ha_pass': {'queue': 'fast'},
    'apps.domains.tasks.reverify_custom_domains_task': {'queue': 'fast'},
    'apps.deployments.tasks_ecosystem.ecosystem_scan_task': {'queue': 'deploy'},
    'apps.deployments.tasks_ecosystem.ecosystem_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks_ecosystem.ecosystem_release_wave_task': {'queue': 'fast'},
    'apps.deployments.tasks_ecosystem.ecosystem_deferred_build_task': {'queue': 'fast'},
    'apps.deployments.tasks.deployment.tasks_safedeploy.create_preview_environment_job': {'queue': 'deploy'},
    'apps.deployments.tasks.deployment.tasks_safedeploy.create_database_clone_job': {'queue': 'deploy'},
    'apps.deployments.tasks.deployment.tasks_safedeploy.run_migration_validation_job': {'queue': 'deploy'},
    'apps.deployments.tasks.deployment.tasks_safedeploy.run_preview_tests_job': {'queue': 'deploy'},
    'apps.deployments.tasks.deployment.tasks_safedeploy.provision_preview_service_job': {'queue': 'deploy'},
    'apps.deployments.tasks.deployment.tasks_safedeploy.run_preview_health_check_job': {'queue': 'deploy'},
    'apps.deployments.tasks.deployment.tasks_safedeploy.expire_stale_previews_job': {'queue': 'fast'},
    'apps.deployments.tasks.deployment.tasks_safedeploy.destroy_preview_environment_job': {'queue': 'deploy'},
    'apps.deployments.tasks.update_remote_server_task': {'queue': 'deploy'},
    'apps.deployments.tasks_deploy_remote.self_heal_remote_deployment': {'queue': 'deploy'},
    'apps.deployments.tasks.delete_service_task': {'queue': 'deploy'},
    'apps.deployments.tasks.recover_stalled_deletions': {'queue': 'deploy'},
    'apps.deployments.tasks.recover_redis_failover': {'queue': 'deploy'},
    'apps.deployments.tasks.auto_promote_staged_deployments': {'queue': 'deploy'},
    'apps.deployments.tasks.auto_review_deployments': {'queue': 'deploy'},
    # -- Tasks re-exported from specialized modules (name= resolves to tasks.*) --
    'apps.deployments.tasks.provision_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.deprovision_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.backup_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.restore_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.delete_addon_task': {'queue': 'deploy'},
    'apps.addons.tasks.addon_health_check_all': {'queue': 'deploy'},
    'apps.addons.tasks.addon_auto_vacuum': {'queue': 'deploy'},
    'apps.addons.tasks.rotate_addon_credentials_task': {'queue': 'deploy'},
    'apps.deployments.tasks.execute_server_transfer_task': {'queue': 'deploy'},
    'apps.deployments.tasks.rollback_transfer_task': {'queue': 'deploy'},
    'apps.deployments.tasks.platform_update_task': {'queue': 'deploy'},
    'apps.deployments.tasks.platform_rollback_task': {'queue': 'deploy'},
    # -- Docker-dependent tasks previously falling to default 'celery' queue --
    'apps.deployments.tasks.run_maintenance_task': {'queue': 'deploy'},
    'apps.deployments.tasks.registry_garbage_collection_task': {'queue': 'deploy'},
    'apps.deployments.tasks_spiffe.sync_spiffe_entries_task': {'queue': 'deploy'},
    'apps.mtls.tasks.inject_mtls_task': {'queue': 'deploy'},
    'apps.deployments.services.provisioner.provision_server': {'queue': 'deploy'},
    'apps.core.services.health_monitor.monitor_health_task': {'queue': 'deploy'},
    'apps.autoscaler.services.legacy_autoscaler.check_autoscale_task': {'queue': 'deploy'},
    'apps.autoscaler.services.tasks_autoscale.analyze_all_services_task': {'queue': 'deploy'},
    'apps.autoscaler.services.tasks_autoscale.cleanup_stuck_spawning': {'queue': 'deploy'},
    # Stats collection does Docker/K8s I/O and mutates platform containers —
    # keep it off the default 'celery' queue with the other I/O-heavy tasks.
    'apps.autoscaler.tasks.autoscaler_collect_stats': {'queue': 'deploy'},
    'apps.deployments.tasks.infra.tasks_mesh.check_mesh_health_task': {'queue': 'deploy'},
    'apps.deployments.tasks.infra.tasks_mesh.deploy_mesh_task': {'queue': 'deploy'},
    'apps.deployments.tasks_replication.deploy_replication_task': {'queue': 'deploy'},
    'apps.deployments.tasks_replication.manual_failover_task': {'queue': 'deploy'},
    'apps.deployments.tasks_cron.trigger_cron_job': {'queue': 'deploy'},
    'apps.core.tasks.metrics.collect_metrics_task': {'queue': 'deploy'},
    'apps.core.tasks.metrics.cleanup_build_cache_task': {'queue': 'deploy'},
    'apps.core.tasks.alerts.scan_running_containers_logs_task': {'queue': 'deploy'},
    'apps.notifications.tasks.evaluate_alert_rules_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.create_service_backup_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.create_server_backup_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.restore_service_backup_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.restore_server_backup_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.purge_user_backups_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.cleanup_old_backups_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.run_scheduled_backups_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.run_scheduled_snapshots_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.create_snapshot_task': {'queue': 'deploy'},
    'apps.deployments.tasks_backup.archive_old_deployment_logs_task': {'queue': 'deploy'},
    'apps.deployments.tasks_bundles.provision_bundle_task': {'queue': 'deploy'},
    'apps.deployments.tasks_bundles.deprovision_bundle_task': {'queue': 'deploy'},
    'apps.deployments.tasks_bundles.delete_bundle_task': {'queue': 'deploy'},
    'apps.deployments.tasks_bundles.backup_bundle_component_task': {'queue': 'deploy'},
    'apps.deployments.tasks_bundles.restore_bundle_component_task': {'queue': 'deploy'},
    'apps.deployments.tasks.one_click_deploy_template_task': {'queue': 'deploy'},
    'apps.deployments.tasks.node_watchdog_task': {'queue': 'deploy'},
    'apps.deployments.services.heartbeat_bus.persist_heartbeats_task': {'queue': 'fast'},
    'apps.cloud.services.ssl_monitor.check_ssl_certificates_task': {'queue': 'deploy'},
    'apps.deployments.tasks._post_deploy_monitor': {'queue': 'deploy'},
    'apps.permissions.tasks.deactivate_expired_memberships': {'queue': 'deploy'},
    'apps.intelligence.tasks.detect_anomalies_task': {'queue': 'deploy'},
    'apps.intelligence.tasks.proactive_health_scan_task': {'queue': 'deploy'},
    'apps.intelligence.tasks.daily_intelligence_report_task': {'queue': 'deploy'},
    'apps.intelligence.tasks.ai_deployment_review_task': {'queue': 'deploy'},
    'apps.deployments.tasks.ai.tasks_ai.analyze_failure_task': {'queue': 'deploy'},
    'apps.deployments.tasks.ai.tasks_code_intelligence.deep_scan_and_verify_task': {'queue': 'deploy'},
    'apps.deployments.tasks.check_agent_heartbeats_task': {'queue': 'deploy'},
}

app.conf.beat_schedule = {
    # Collect real Docker stats every 60 seconds
    'collect-metrics-every-60s': {
        'task': 'apps.core.tasks.metrics.collect_metrics_task',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Check service health every 30 seconds
    'monitor-health-every-30s': {
        'task': 'apps.core.services.health_monitor.monitor_health_task',
        'schedule': 30.0,
        'options': {'expires': 30.0},
    },
    # Check autoscale thresholds every 30 seconds
    'check-autoscale-every-30s': {
        'task': 'apps.autoscaler.services.legacy_autoscaler.check_autoscale_task',
        'schedule': 30.0,
        'options': {'expires': 30.0},
    },
    # Collect stats for inline autoscaler dashboard every 60 seconds
    'autoscaler-collect-stats-every-60s': {
        'task': 'apps.autoscaler.tasks.autoscaler_collect_stats',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Persist cluster heartbeat snapshots to DB every 60 seconds
    'persist-heartbeats-every-60s': {
        'task': 'apps.deployments.services.heartbeat_bus.persist_heartbeats_task',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Mark interrupted/stale server provisioning runs as failed
    'cleanup-stale-server-provisioning-every-5m': {
        'task': 'apps.deployments.services.provisioner.cleanup_stale_server_provisioning',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Remove empty project/ecosystem bridges left by failed or timed-out
    # deploys. The task never removes networks with containers or DB scopes.
    'cleanup-scoped-networks-every-30m': {
        'task': 'apps.deployments.tasks.cleanup_scoped_networks_task',
        'schedule': 1800.0,
        'options': {'expires': 1800.0},
    },
    # Self-heal scoped-network isolation (stale iptables rules, recreated
    # bridges missing egress, Traefik bridge membership) every 10 minutes
    'reconcile-network-isolation-every-10m': {
        'task': 'apps.deployments.tasks.reconcile_network_isolation_task',
        'schedule': 600.0,
        'options': {'expires': 600.0},
    },
    # Crash-loop detector: pages on containers stuck restarting (the 2026-08
    # redis-addon outage looped 1875x unnoticed)
    'container-restart-watchdog-every-5m': {
        'task': 'apps.deployments.tasks.container_restart_loop_watchdog_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Alias-aware sweep of addon containers without DB records
    'orphan-addon-gc-hourly': {
        'task': 'apps.deployments.tasks.orphan_addon_gc_task',
        'schedule': 3600.0,
        'options': {'expires': 3600.0},
    },
    # Orphaned runtime-container sweep: stale green candidates (running
    # OR stopped) not referenced by any deployment, expired rollback
    # backups, and containers for DB-missing services/addons. Before this
    # was scheduled, greens from crashed promotes stayed "Up (unhealthy)"
    # for days — the old rule only ever collected STOPPED containers.
    'cleanup-orphaned-containers-every-30m': {
        'task': 'apps.deployments.tasks.cleanup_orphaned_containers_task',
        'schedule': 1800.0,
        'options': {'expires': 1800.0},
    },
    # Edge Shield watchdog: BGP/DNS hijack symptom detection — multi-
    # vantage DNS (answers must be Cloudflare edges, never the origin
    # IP), RPKI validity of the covering aggregate. Pages via the
    # standard alert pipeline on the first anomaly.
    'edge-shield-watchdog-every-5m': {
        'task': 'apps.deployments.tasks.edge_shield_watchdog',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Ghost EcosystemPlan unblocker: plans stuck in scanning/deploying
    # with dead Celery tasks lock the ecosystem UI behind 429s forever
    # (the scan guard sees them as "already in progress"). Clear them.
    'recover-stale-ecosystem-plans-every-10m': {
        'task': 'apps.deployments.tasks.recover_stale_ecosystem_plans',
        'schedule': 600.0,
        'options': {'expires': 600.0},
    },
    # Ghost ServerTransfer unblocker: transfers stuck in active states
    # with dead Celery tasks lock the transfer UI behind 409s forever.
    'recover-stale-transfers-every-15m': {
        'task': 'apps.deployments.tasks.recover_stale_transfers',
        'schedule': 900.0,
        'options': {'expires': 900.0},
    },
    # AUTO-RECOVERY for missing migrations: the 2026-09-02 multi-outage
    # was caused by migration 0196 never being applied on the VPS — the
    # PlatformConfig table was missing a column, every ORM query hit
    # ProgrammingError, the config loader fell to its ghost path with an
    # empty domain, and the Caddyfile lost the platform block (525 +
    # custom domains demoted). This beat task detects and applies pending
    # migrations automatically — cheap no-op when up to date.
    'ensure-migrations-applied-every-5m': {
        'task': 'apps.deployments.tasks.ensure_migrations_applied',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Service HA pass: evaluate ha_mode != 'none' services every 60s.
    # Gated by PlatformConfig.service_ha_enabled — a cheap no-op when the
    # master toggle is off. Per-service opt-in: 'local' = same-node
    # replica reconcile, 'remote' = cross-node failover.
    'service-ha-pass-every-60s': {
        'task': 'apps.deployments.tasks.service_ha_pass',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Continuous custom-domain re-verification (anti-hijack): a domain
    # that stops pointing at the platform loses routing + on-demand TLS
    # eligibility. Verification was previously one-shot — a domain
    # verified once kept its cert forever, even after the owner
    # repointed DNS at an attacker (or an attacker flip-flopped DNS
    # after passing the check once).
    'reverify-custom-domains-hourly': {
        'task': 'apps.domains.tasks.reverify_custom_domains_task',
        'schedule': 3600.0,
        'options': {'expires': 3600.0},
    },
    # Re-queue services stuck in DELETION_PENDING (worker crash, Docker hang, etc.)
    'recover-stalled-deletions-every-5m': {
        'task': 'apps.deployments.tasks.recover_stalled_deletions',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Cancel deployments stalled with no live worker (ghost worker, lost
    # message, timed-out wave that moved on). Never touches rows owned by
    # a deploying ecosystem plan, human-gated AWAITING_APPROVAL, or STAGED.
    'recover-stalled-deployments-every-15m': {
        'task': 'apps.deployments.tasks.recover_stalled_deployments',
        'schedule': 900.0,
        'options': {'expires': 900.0},
    },
    # Clean up replicas stuck in SPAWNING for > 5 minutes (failed spawn)
    'cleanup-stuck-spawning-every-5m': {
        'task': 'apps.autoscaler.services.tasks_autoscale.cleanup_stuck_spawning',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Apply VPA soft limits + hard ceiling to running containers every 5 minutes
    'apply-vpa-limits-every-5m': {
        'task': 'apps.autoscaler.services.tasks_autoscale.apply_vpa_limits_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Redis Sentinel owns failover. Do not run the legacy recovery task here:
    # it can mistake a hostname/IP representation difference for an orphan
    # and delete a healthy primary (incident 2026-09-05). Recovery is now
    # manual until the task is redesigned around Sentinel's role/epoch.
    # Addon HA watchdog: replica health + Postgres auto-failover
    'addon-ha-watchdog-every-30s': {
        'task': 'apps.addons.tasks.ha_watchdog.check_addon_ha_task',
        'schedule': 30.0,
        'options': {'expires': 30.0},
    },
    # Auto-promote deployments stuck in STAGED for > configured hours
    'auto-promote-staged-every-15m': {
        'task': 'apps.deployments.tasks.auto_promote_staged_deployments',
        'schedule': 900.0,
        'options': {'expires': 600.0},
    },
    # Auto-approve deployments stuck in REVIEW for > configured hours
    'auto-review-pending-every-15m': {
        'task': 'apps.deployments.tasks.auto_review_deployments',
        'schedule': 900.0,
        'options': {'expires': 600.0},
    },
    # Intelligence runtime anomaly scan every 3 minutes
    'detect-runtime-anomalies-every-180s': {
        'task': 'apps.intelligence.tasks.detect_anomalies_task',
        'schedule': 180.0,
        'options': {'expires': 180.0},
    },
    # ── Billing & Revenue Tasks ──────────────────────────────────────────────
    # Snapshot active services and calculate cost hourly
    'billing-collect-usage-hourly': {
        'task': 'apps.billing.tasks.collect_usage_task',
        'schedule': crontab(minute=0),
        'options': {'expires': 3600.0},
    },
    # Generate invoices on the 1st of each month
    'billing-generate-invoices-monthly': {
        'task': 'apps.billing.tasks.generate_monthly_invoices',
        'schedule': crontab(minute=0, hour=0, day_of_month='1'),
        'options': {'expires': 86400.0},
    },
    # Send payment reminders daily
    'billing-send-reminders-daily': {
        'task': 'apps.billing.tasks.send_payment_reminders',
        'schedule': crontab(minute=0, hour=8),
        'options': {'expires': 3600.0},
    },
    # Snapshot yesterday's revenue at midnight
    'billing-aggregate-daily-revenue': {
        'task': 'apps.billing.tasks.aggregate_daily_revenue',
        'schedule': crontab(minute=0, hour=0),
        'options': {'expires': 3600.0},
    },
    # Pull cloud infrastructure costs at midnight
    'billing-calculate-infrastructure-costs': {
        'task': 'apps.billing.tasks.calculate_infrastructure_costs',
        'schedule': crontab(minute=0, hour=0),
        'options': {'expires': 3600.0},
    },
    # SSL certificate expiry scan every 6 hours
    'check-ssl-certificates-every-6h': {
        'task': 'apps.cloud.services.ssl_monitor.check_ssl_certificates_task',
        'schedule': 21600.0,
        'options': {'expires': 1800.0},
    },
    # Cleanup Docker build cache daily
    'cleanup-build-cache-daily': {
        'task': 'apps.core.tasks.metrics.cleanup_build_cache_task',
        'schedule': 86400.0,  # 24 hours
        'options': {'expires': 86400.0},
    },
    # WireGuard mesh health check every 60 seconds
    'mesh-health-check-every-60s': {
        'task': 'apps.deployments.tasks.infra.tasks_mesh.check_mesh_health_task',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Leader election heartbeat every 5 seconds
    'cluster-heartbeat-every-5s': {
        'task': 'apps.deployments.tasks_election.heartbeat_task',
        'schedule': 5.0,
        'options': {'expires': 10.0},
    },
    # Cleanup old heartbeat logs every 10 minutes (deterministic)
    'cleanup-heartbeat-logs-every-10m': {
        'task': 'apps.deployments.tasks_election.cleanup_heartbeat_logs_task',
        'schedule': 600.0,
        'options': {'expires': 600.0},
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
        'task': 'apps.core.tasks.alerts.scan_running_containers_logs_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Evaluate platform alert rules every 5 minutes
    'evaluate-alert-rules-every-5m': {
        'task': 'apps.notifications.tasks.evaluate_alert_rules_task',
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
    # Agent registrar heartbeat check every 60s. Detects silent
    # agent outages even when the master's /health probe is
    # still passing (e.g. the agent's gunicorn is up but its
    # celery worker is wedged).
    'check-agent-heartbeats-every-60s': {
        'task': 'apps.deployments.tasks.check_agent_heartbeats_task',
        'schedule': 60.0,
        'options': {'expires': 60.0},
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
        'task': 'apps.autoscaler.services.tasks_autoscale.analyze_all_services_task',
        'schedule': 180.0,
        'options': {'expires': 180.0},
    },
    # Run scheduled backups every 1 minute
    'run-scheduled-backups-every-1m': {
        'task': 'apps.deployments.tasks_backup.run_scheduled_backups_task',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Run scheduled snapshots every 1 minute
    'run-scheduled-snapshots-every-1m': {
        'task': 'apps.deployments.tasks_backup.run_scheduled_snapshots_task',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Clean up expired backups every 6 hours
    'cleanup-old-backups-every-6h': {
        'task': 'apps.deployments.tasks_backup.cleanup_old_backups_task',
        'schedule': 21600.0,
        'options': {'expires': 21600.0},
    },
    # Archive + truncate build/runtime logs of old terminal deployments
    # daily (04:00): Postgres holds unbounded log text, so offload the
    # full text to S3 when a platform destination exists, else truncate.
    'archive-old-deployment-logs-daily': {
        'task': 'apps.deployments.tasks_backup.archive_old_deployment_logs_task',
        'schedule': crontab(hour=4, minute=0),
        'options': {'expires': 3600.0},
    },
    # Verify backup integrity daily (random sample of COMPLETED backups)
    'verify-backup-integrity-daily': {
        'task': 'apps.deployments.tasks_backup.verify_backup_integrity_task',
        'schedule': crontab(hour=3, minute=30),
        'options': {'expires': 3600.0},
    },
    # Dispatch due cron jobs every minute
    'check-cron-jobs-every-1m': {
        'task': 'apps.deployments.tasks_cron.check_cron_jobs',
        'schedule': 60.0,
        'options': {'expires': 60.0},
    },
    # Expire stale preview environments hourly
    'expire-stale-previews': {
        'task': 'apps.deployments.tasks.deployment.tasks_safedeploy.expire_stale_previews_job',
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
    # Sync SPIRE mTLS registration entries every 5 minutes
    'sync-spiffe-entries-every-5m': {
        'task': 'apps.deployments.tasks_spiffe.sync_spiffe_entries_task',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Monitor stuck auto-rollback heartbeats every 5 minutes.
    # Alerts when a rollback deployment stays QUEUED for too long
    # (broker down, worker wedged, etc.).
    'monitor-stuck-rollback-heartbeats-every-5m': {
        'task': 'apps.deployments.services.auto_rollback.monitor_stuck_rollback_heartbeats',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Deactivate expired team, org, and project memberships daily
    'deactivate-expired-memberships-daily': {
        'task': 'apps.permissions.tasks.deactivate_expired_memberships',
        'schedule': 86400.0,
        'options': {'expires': 86400.0},
    },
    # Collect Traefik access log entries every 15 seconds
    'collect-traffic-logs-every-15s': {
        'task': 'apps.core.tasks.traffic.collect_traefik_logs',
        'schedule': 15.0,
        'options': {'expires': 15.0, 'queue': 'fast'},
    },
    # Resolve IP geolocations every 30 seconds
    'resolve-traffic-geolocations-every-30s': {
        'task': 'apps.core.tasks.traffic.resolve_traffic_geolocations',
        'schedule': 30.0,
        'options': {'expires': 30.0, 'queue': 'fast'},
    },
    # ── Addon Tasks ──────────────────────────────────────────────────────────
    # Health-check all active addons every 5 minutes and dispatch alerts
    'addon-health-check-all-every-5m': {
        'task': 'apps.addons.tasks.addon_health_check_all',
        'schedule': 300.0,
        'options': {'expires': 300.0},
    },
    # Weekly VACUUM ANALYZE on all Postgres addons (Sunday 03:15 UTC)
    'addon-auto-vacuum-weekly': {
        'task': 'apps.addons.tasks.addon_auto_vacuum',
        'schedule': crontab(minute=15, hour=3, day_of_week=0),
        'options': {'expires': 86400.0},
    },
}

# Media node tasks are opt-in — only run when SMSLY_ENABLE_MEDIA_NODES is set.
# This avoids unnecessary CPU load and orphan queues on hosts that don't
# run media nodes.
if os.environ.get('SMSLY_ENABLE_MEDIA_NODES'):
    app.conf.beat_schedule.update({
        'check-stale-media-nodes-every-30s': {
            'task': 'apps.media.tasks.check_stale_media_nodes',
            'schedule': 30.0,
            'options': {'expires': 30.0, 'queue': 'media-telemetry'},
        },
        'aggregate-media-capacity-every-60s': {
            'task': 'apps.media.tasks.aggregate_media_capacity',
            'schedule': 60.0,
            'options': {'expires': 60.0, 'queue': 'media-telemetry'},
        },
        'flush-media-telemetry-to-db-every-5m': {
            'task': 'apps.media.tasks.flush_telemetry_to_db',
            'schedule': 300.0,
            'options': {'expires': 300.0, 'queue': 'media-telemetry'},
        },
        'rotate-media-node-keys-daily': {
            'task': 'apps.media.tasks.rotate_media_node_keys',
            'schedule': crontab(hour=2, minute=0),
            'options': {'expires': 3600.0, 'queue': 'deploy'},
        },
        'verify-federation-chains-hourly': {
            'task': 'apps.media.tasks.verify_federation_chains',
            'schedule': 3600.0,
            'options': {'expires': 3600.0, 'queue': 'media-audit'},
        },
    })
    app.conf.task_routes.update({
        'apps.media.tasks.check_stale_media_nodes': {'queue': 'media-telemetry'},
        'apps.media.tasks.aggregate_media_capacity': {'queue': 'media-telemetry'},
        'apps.media.tasks.flush_telemetry_to_db': {'queue': 'media-telemetry'},
        'apps.media.tasks.rotate_media_node_keys': {'queue': 'deploy'},
        'apps.media.tasks.verify_federation_chains': {'queue': 'media-audit'},
        'apps.media.tasks.process_media_heartbeat': {'queue': 'media-telemetry'},
        'apps.media.tasks.restart_media_node_services_task': {'queue': 'deploy'},
    })


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
