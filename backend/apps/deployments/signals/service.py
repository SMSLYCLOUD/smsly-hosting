import logging
import secrets

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from ..models import EnvironmentVariable, Service
from ..utils import log_event

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Service)
def create_default_env_vars(_sender, instance, created, **kwargs):
    if created:
        api_key = f"smsly_{secrets.token_urlsafe(32)}"
        EnvironmentVariable.objects.create(
            service=instance,
            key='SMSLY_API_KEY',
            value=api_key,
            is_secret=True,
        )


@receiver(post_save, sender=Service)
def audit_service_lifecycle(_sender, instance, created, **kwargs):
    if created:
        log_event(
            actor=instance.owner.get_username() if instance.owner else 'system',
            action='SERVICE_CREATE',
            target=f'Service: {instance.name}',
            metadata={
                'service_id': str(instance.id),
                'deploy_type': instance.deploy_type,
                'stack': getattr(instance, 'buildpack', 'unknown'),
                'resources': {
                    'cpu': float(instance.cpu_cores),
                    'memory_mb': instance.memory_mb,
                },
                'network': {
                    'port': instance.internal_port,
                    'domain': instance.public_domain,
                },
            },
        )


@receiver(post_save, sender=Service)
def regenerate_caddyfile_on_service_change(_sender, instance, created, **kwargs):
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'public_domain' not in update_fields and 'custom_domains' not in update_fields:
        return
    try:
        from apps.deployments.tasks.deploy.helpers import _regenerate_caddyfile
        _regenerate_caddyfile()
    except Exception as exc:
        logger.warning("Could not regenerate Caddyfile from Service signal: %s", exc)


@receiver(post_save, sender=Service)
def broadcast_service_status_change(_sender, instance, created, **kwargs):
    if not created:
        try:
            from config.metrics import SERVICES_ACTIVE
            SERVICES_ACTIVE.set(
                Service.objects.filter(status=Service.Status.ACTIVE).count()
            )
        except Exception as exc:
            logging.getLogger(__name__).debug("smsly_services_active update failed: %s", exc)

    if created:
        return

    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return

    latest_deployment = instance.deployments.order_by('-created_at').first()

    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    if channel_layer:
        user_ids = set()
        if instance.owner_id:
            user_ids.add(instance.owner_id)
        if instance.project_id and instance.project.team_id:
            from apps.teams.models import TeamMember
            member_ids = TeamMember.objects.filter(
                team_id=instance.project.team_id,
            ).values_list('user_id', flat=True)
            user_ids.update(member_ids)
        try:
            for uid in user_ids:
                async_to_sync(channel_layer.group_send)(
                    f"user_services_{uid}",
                    {
                        'type': 'service_status_update',
                        'service_id': str(instance.id),
                        'service_name': instance.name,
                        'status': instance.status,
                        'deployment_status': latest_deployment.status if latest_deployment else 'unknown',
                        'updated_at': instance.updated_at.isoformat(),
                    }
                )
        except Exception as e:
            logging.getLogger(__name__).warning(
                "Failed to broadcast service status update for %s: %s", instance.id, e
            )


@receiver(post_delete, sender=Service)
def regenerate_caddyfile_on_service_deletion(_sender, instance, **kwargs):
    logger = logging.getLogger(__name__)
    try:
        from apps.deployments.tasks.deploy.helpers import _regenerate_caddyfile
        _regenerate_caddyfile()
        logger.info("Caddyfile regenerated after service %s deletion", instance.name)
    except Exception as exc:
        logger.warning("Could not regenerate Caddyfile after service deletion: %s", exc)
