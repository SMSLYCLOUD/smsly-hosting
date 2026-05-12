import logging
import os
import secrets

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Service, Deployment, EnvironmentVariable, PlatformConfig
from .models_audit import AuditLog
from .utils import log_event
from services.caddy_manager import generate_caddyfile, apply_caddyfile


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


@receiver(post_save, sender=PlatformConfig)
def sync_infrastructure_on_config_change(sender, instance, **kwargs):
    """
    Update Caddy configuration and system environment when PlatformConfig changes.
    This enables full UI autonomy for domain and SSL management.
    Also syncs domain back to .env so future --update runs pick up the correct values.
    """
    logger = logging.getLogger(__name__)
    try:
        # 1. Update ALLOWED_HOSTS in memory
        from apps.deployments.patching import patch_runtime_settings
        patch_runtime_settings()

        # 2. Sync domain back to .env so future --update runs pick it up
        _env_path = "/app/.env"
        _new_domain = (instance.domain or "").strip()
        _new_ssl = instance.use_ssl
        if _new_domain and os.path.isfile(_env_path):
            _updated = False
            _lines = []
            with open(_env_path, "r", encoding="utf-8") as _fh:
                for _line in _fh:
                    if _line.startswith("DOMAIN="):
                        _lines.append(f"DOMAIN={_new_domain}\n")
                        _updated = True
                    elif _line.startswith("USE_SSL="):
                        _lines.append(f"USE_SSL={'true' if _new_ssl else 'false'}\n")
                        _updated = True
                    else:
                        _lines.append(_line)
            if not any(l.startswith("DOMAIN=") for l in _lines):
                _lines.append(f"DOMAIN={_new_domain}\n")
                _updated = True
            if not any(l.startswith("USE_SSL=") for l in _lines):
                _lines.append(f"USE_SSL={'true' if _new_ssl else 'false'}\n")
                _updated = True
            if _updated:
                with open(_env_path, "w", encoding="utf-8") as _fh:
                    _fh.writelines(_lines)
                logger.info("Synced .env: DOMAIN=%s, USE_SSL=%s", _new_domain, _new_ssl)

        # 3. Re-generate and apply Caddyfile
        logger.info("Signal: Re-generating Caddyfile for domain %s", instance.domain)
        content = generate_caddyfile(instance)
        apply_caddyfile(
            content,
            cloudflare_token=instance.cloudflare_api_token,
            preserve_existing_token=True
        )

        # 4. Log the event
        log_event(
            actor='system',
            action='INFRA_SYNC',
            target='Caddyfile',
            metadata={
                'domain': instance.domain,
                'use_ssl': instance.use_ssl,
                'wildcard': instance.wildcard_subdomains,
            }
        )
    except Exception as e:
        logger.error("Failed to sync infrastructure from signal: %s", e)


@receiver(post_save, sender=PlatformConfig)
def update_allowed_hosts_on_config_change(sender, instance, **kwargs):
    # This is now handled by sync_infrastructure_on_config_change
    pass
