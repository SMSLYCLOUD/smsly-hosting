from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Service, Deployment, EnvironmentVariable
from .models_audit import AuditLog
import secrets


@receiver(post_save, sender=Service)
def create_default_env_vars(sender, instance, created, **kwargs):
    if created:
        # Inject SMSLY_API_KEY
        api_key = f"smsly_{secrets.token_urlsafe(32)}"
        EnvironmentVariable.objects.create(
            service=instance,
            key='SMSLY_API_KEY',
            value=api_key,
            is_secret=True
        )


from .utils import log_event


@receiver(post_save, sender=Service)
def create_default_env_vars(sender, instance, created, **kwargs):
    if created:
        # Inject SMSLY_API_KEY
        api_key = f"smsly_{secrets.token_urlsafe(32)}"
        EnvironmentVariable.objects.create(
            service=instance,
            key='SMSLY_API_KEY',
            value=api_key,
            is_secret=True
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
                    'memory_mb': instance.memory_mb
                },
                'network': {
                    'port': instance.internal_port,
                    'domain': instance.public_domain
                }
            },
        )


@receiver(post_save, sender=Deployment)
def audit_deployment_lifecycle(sender, instance, created, **kwargs):
    """Log deployment lifecycle events with exhaustive metadata."""
    if created:
        log_event(
            actor=instance.service.owner.get_username() if instance.service.owner else 'system',
            action='DEPLOY_TRIGGER',
            target=f'Service: {instance.service.name}',
            metadata={
                'deployment_id': str(instance.id),
                'service_id': str(instance.service.id),
                'commit_hash': instance.commit_hash,
                'commit_message': getattr(instance, 'commit_message', ''),
                'is_rollback': instance.is_rollback,
                'ai_assisted': bool(getattr(instance, 'ai_diagnosis', None))
            },
        )
    else:
        # Only log terminal status transitions — not every save.
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'status' not in update_fields:
            return

        if instance.status in (
            Deployment.Status.ACTIVE,
            Deployment.Status.FAILED,
            Deployment.Status.CANCELLED,
        ):
            log_event(
                actor=instance.service.owner.get_username() if instance.service.owner else 'system',
                action=f'DEPLOY_{instance.status}',
                target=f'Service: {instance.service.name}',
                metadata={
                    'deployment_id': str(instance.id),
                    'service_id': str(instance.service.id),
                    'status': instance.status,
                    'diagnosis': getattr(instance, 'ai_diagnosis', None) if instance.status == Deployment.Status.FAILED else None
                },
            )

