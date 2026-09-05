import logging
import secrets

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from ..models import EnvironmentVariable, Service
from ..utils import log_event

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Service)
def create_default_env_vars(sender, instance, created, **kwargs):
    if created:
        api_key = f"smsly_{secrets.token_urlsafe(32)}"
        EnvironmentVariable.objects.create(
            service=instance,
            key='SMSLY_API_KEY',
            value=api_key,
            is_secret=True,
        )


@receiver(post_save, sender=Service)
def audit_service_lifecycle(sender, instance, created, **kwargs):
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
def regenerate_caddyfile_on_service_change(sender, instance, created, **kwargs):
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'public_domain' not in update_fields and 'custom_domains' not in update_fields and 'staging_domain' not in update_fields and 'host_aliases' not in update_fields and 'path_redirects' not in update_fields:
        return
    try:
        from apps.deployments.tasks.deploy.helpers import _regenerate_caddyfile
        _regenerate_caddyfile()
    except Exception as exc:
        logger.warning("Could not regenerate Caddyfile from Service signal: %s", exc)


# TODO: Does DB queries (TeamMember filter) + WebSocket sends. Consider
# dispatching WebSocket broadcast to a Celery task.
@receiver(post_save, sender=Service)
def broadcast_service_status_change(sender, instance, created, **kwargs):
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


@receiver(post_save, sender=Service)
def apply_resource_limits_live(sender, instance, created, **kwargs):
    """Apply CPU/RAM limit changes to running containers without a redeploy.

    Only fires when cpu_cores/memory_mb were part of the save (or on a
    full save where the fields can't be ruled out). Dispatches a celery
    task — never blocks the save on Docker. New services are skipped:
    their containers don't exist yet and the deploy applies limits.
    """
    if created:
        return
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'cpu_cores' not in update_fields and 'memory_mb' not in update_fields:
        return
    try:
        from apps.deployments.tasks.resource_limits import apply_service_resource_limits_task
        apply_service_resource_limits_task.delay(str(instance.id))
    except Exception as exc:
        logger.warning("Could not dispatch live limit apply for service %s: %s", instance.id, exc)


@receiver(post_delete, sender=Service)
def regenerate_caddyfile_on_service_deletion(sender, instance, **kwargs):
    logger = logging.getLogger(__name__)
    try:
        from apps.deployments.tasks.deploy.helpers import _regenerate_caddyfile
        _regenerate_caddyfile()
        logger.info("Caddyfile regenerated after service %s deletion", instance.name)
    except Exception as exc:
        logger.warning("Could not regenerate Caddyfile after service deletion: %s", exc)

    # Prune stale CORS origins from deleted service domains
    try:
        from django.conf import settings
        import re
        deleted_domain = (instance.public_domain or "").strip()
        if deleted_domain:
            origin_https = f"https://{deleted_domain}"
            origin_http = f"http://{deleted_domain}"
            for origin in [origin_https, origin_http]:
                if origin in settings.CORS_ALLOWED_ORIGINS:
                    settings.CORS_ALLOWED_ORIGINS.remove(origin)
                    logger.info("Pruned stale CORS origin: %s", origin)
                if origin in settings.CSRF_TRUSTED_ORIGINS:
                    settings.CSRF_TRUSTED_ORIGINS.remove(origin)
                    logger.info("Pruned stale CSRF origin: %s", origin)
        # Also prune any custom domains
        for custom_domain in (instance.custom_domains or []):
            if isinstance(custom_domain, str) and custom_domain.strip():
                for origin in [f"https://{custom_domain.strip()}", f"http://{custom_domain.strip()}"]:
                    if origin in settings.CORS_ALLOWED_ORIGINS:
                        settings.CORS_ALLOWED_ORIGINS.remove(origin)
                    if origin in settings.CSRF_TRUSTED_ORIGINS:
                        settings.CSRF_TRUSTED_ORIGINS.remove(origin)
    except Exception as exc:
        logger.debug("CORS origin pruning skipped: %s", exc)
