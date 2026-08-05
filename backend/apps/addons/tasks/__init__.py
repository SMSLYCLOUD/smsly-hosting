from __future__ import annotations

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM, TASK_TIME_LIMIT_STANDARD
from apps.deployments.models.addons import Addon

from apps.addons.services.alerts import check_alerts
from apps.addons.services.maintenance import AddonMaintenanceService


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
def addon_health_check_all(self) -> None:
    """Run health checks on all active addons."""
    import logging
    _logger = logging.getLogger(__name__)
    try:
        for addon in Addon.objects.filter(status='ACTIVE').select_related('service__owner'):
            service = AddonMaintenanceService(addon)
            service.health_check()
            # Dispatch alerts for unhealthy addons based on metrics
            try:
                stats = service.proxy.get_stats()
                alerts = check_alerts(addon, metrics=stats)
                for alert in alerts:
                    from apps.notifications.tasks import dispatch_notification
                    dispatch_notification.delay(
                        event_type='addon_alert',
                        user_id=str(addon.service.owner_id) if addon.service else None,
                        title=f"Addon Alert: {alert.get('message', 'Unknown')}",
                        message=f"Addon {addon.name}: {alert.get('message', '')}",
                    )
            except Exception:
                pass  # Alert dispatch is best-effort
    except Exception as exc:
        _logger.error("addon_health_check_all failed: %s", exc)
        raise

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
    except Exception as exc:
        _logger.error("rotate_addon_credentials_task failed for addon %s: %s", addon_id, exc)
        raise
