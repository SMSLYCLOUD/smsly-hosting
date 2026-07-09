from celery import shared_task

from apps.deployments.models_addons import Addon

from .services.alerts import check_alerts
from .services.maintenance import AddonMaintenanceService


@shared_task
def addon_health_check_all():
    """Run health checks on all active addons."""
    for addon in Addon.objects.filter(status='ACTIVE'):
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

@shared_task
def addon_auto_vacuum():
    """Weekly VACUUM ANALYZE on all Postgres addons."""
    for addon in Addon.objects.filter(status='ACTIVE', addon_type='POSTGRES'):
        service = AddonMaintenanceService(addon)
        service.vacuum_analyze()

@shared_task
def rotate_addon_credentials_task(addon_id):
    """Rotate DB credentials and restart dependent service."""
    addon = Addon.objects.get(id=addon_id)
    service = AddonMaintenanceService(addon)
    service.rotate_credentials()
