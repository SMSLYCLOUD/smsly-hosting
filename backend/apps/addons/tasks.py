from celery import shared_task
from apps.deployments.models_addons import Addon
from .services.maintenance import AddonMaintenanceService

@shared_task
def addon_health_check_all():
    """Run health checks on all active addons."""
    for addon in Addon.objects.filter(status='ACTIVE'):
        service = AddonMaintenanceService(addon)
        health = service.health_check()
        # Could update addon.status or health_status field here
        pass

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
