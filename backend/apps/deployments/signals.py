from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Service, Deployment, EnvironmentVariable
from .models_audit import AuditLog
from .utils import log_event
import secrets


@receiver(post_save, sender=Service)
def create_default_env_vars(sender, instance, created, **kwargs):
    """Inject a unique SMSLY_API_KEY for every new service."""
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
    """Log service creation to audit trail with exhaustive metadata."""
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


@receiver(post_save, sender=Deployment)
def audit_deployment_lifecycle(sender, instance, created, **kwargs):
    """Log deployment lifecycle events and dispatch user notifications on terminal states."""
    owner = instance.service.owner if instance.service.owner else None

    if created:
        log_event(
            actor=owner.get_username() if owner else 'system',
            action='DEPLOY_TRIGGER',
            target=f'Service: {instance.service.name}',
            metadata={
                'deployment_id': str(instance.id),
                'service_id': str(instance.service.id),
                'commit_hash': instance.commit_hash,
                'commit_message': getattr(instance, 'commit_message', ''),
                'is_rollback': instance.is_rollback,
                'ai_assisted': bool(getattr(instance, 'ai_diagnosis', None)),
            },
        )
        return

    # Only act on explicit status field updates to terminal states
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return

    if instance.status not in (
        Deployment.Status.ACTIVE,
        Deployment.Status.FAILED,
        Deployment.Status.CANCELLED,
    ):
        return

    # ── Audit ────────────────────────────────────────────────────────────
    log_event(
        actor=owner.get_username() if owner else 'system',
        action=f'DEPLOY_{instance.status}',
        target=f'Service: {instance.service.name}',
        metadata={
            'deployment_id': str(instance.id),
            'service_id': str(instance.service.id),
            'status': instance.status,
            'diagnosis': getattr(instance, 'ai_diagnosis', None) if instance.status == Deployment.Status.FAILED else None,
        },
    )

    # ── Notify service owner ──────────────────────────────────────────────
    if owner is None:
        return

    try:
        from apps.notifications.tasks import notify_deploy_event
        notify_deploy_event.delay(
            user_id=owner.pk,
            service_name=instance.service.name,
            status='success' if instance.status == Deployment.Status.ACTIVE else 'failed',
            commit_hash=instance.commit_hash or '',
            error=getattr(instance, 'ai_diagnosis', '') or '' if instance.status == Deployment.Status.FAILED else '',
        )
    except Exception as exc:
        # Never let notification failures break the deployment signal chain
        import logging
        logging.getLogger(__name__).warning(
            "Failed to queue deploy notification for user %s: %s", owner.pk, exc
        )
