from __future__ import annotations

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM, TASK_TIME_LIMIT_STANDARD
from apps.core.tasks.coalesce import skip_if_recent
from apps.deployments.models.addons import Addon

from apps.addons.services.alerts import check_alerts
from apps.addons.services.maintenance import AddonMaintenanceService


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
@skip_if_recent("coalesce:addon-health", TASK_TIME_LIMIT_STANDARD[0])
def addon_health_check_all(self) -> None:
    """Run health checks on all active addons with per-addon isolation and alert cooldown."""
    import logging
    from django.core.cache import cache

    _logger = logging.getLogger(__name__)
    for addon in Addon.objects.filter(status='ACTIVE').select_related('service__owner'):
        try:
            service = AddonMaintenanceService(addon)
            health_result = service.health_check() or {}
            stats = health_result.get('details')
            if not isinstance(stats, dict):
                try:
                    stats = service.proxy.get_stats()
                except Exception as stats_exc:
                    stats = {'status': 'OFFLINE', 'error': str(stats_exc)}
            alerts = check_alerts(addon, metrics=stats)
            owner_id = getattr(getattr(addon, 'service', None), 'owner_id', None)
            if not owner_id or not alerts:
                continue
            for alert in alerts:
                cooldown_key = f"addon_alert_cooldown:{addon.id}:{alert.get('message', '')[:40]}"
                if not cache.add(cooldown_key, 1, timeout=1800):
                    continue
                from apps.notifications.tasks import dispatch_notification
                dispatch_notification.delay(
                    event_type='health_alert',
                    user_id=owner_id,
                    title=f"Addon Alert: {alert.get('message', 'Unknown')}",
                    message=f"Addon {addon.name}: {alert.get('message', '')}",
                    metadata={'addon_id': str(addon.id), 'addon_name': addon.name, 'severity': alert.get('severity', 'warning')},
                )
        except Exception as exc:
            _logger.warning("addon_health_check_all failed for addon %s: %s", getattr(addon, 'id', '?'), exc)

@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1])
def addon_auto_vacuum(self) -> None:
    """Weekly VACUUM ANALYZE on all Postgres addons."""
    import logging
    _logger = logging.getLogger(__name__)
    try:
        for addon in Addon.objects.filter(status='ACTIVE', addon_type='POSTGRES').select_related('service'):
            service = AddonMaintenanceService(addon)
            service.vacuum_analyze()
    except Exception as exc:
        _logger.error("addon_auto_vacuum failed: %s", exc)
        raise

@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
def rotate_addon_credentials_task(self, addon_id) -> None:
    """Rotate DB credentials and restart dependent service."""
    import logging
    _logger = logging.getLogger(__name__)
    try:
        addon = Addon.objects.get(id=addon_id)
        service = AddonMaintenanceService(addon)
        result = service.rotate_credentials()
        if result.get('status') == 'failed':
            _logger.error(
                "rotate_addon_credentials_task failed for addon %s: %s",
                addon_id, result.get('error'),
            )
        elif result.get('status') == 'not_implemented':
            _logger.warning(
                "rotate_addon_credentials_task not supported for addon %s (%s)",
                addon_id, addon.addon_type,
            )
        else:
            _logger.info("rotate_addon_credentials_task succeeded for addon %s", addon_id)
            # Tenant pooler: the userlist carries passwords — a rotation
            # without a push leaves stale credentials in the pool.
            try:
                from apps.addons.services.tenant_pooler import push_tenants_config
                push_tenants_config()
            except Exception:
                _logger.debug("tenant pooler push skipped after rotate %s", addon_id)
    except Exception as exc:
        _logger.error("rotate_addon_credentials_task failed for addon %s: %s", addon_id, exc)
        raise
