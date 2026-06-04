import logging
import os
import secrets
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from django.db.models.signals import post_save, post_delete
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
def sync_service_status_on_deployment_change(sender, instance, created, **kwargs):
    """Update service status based on deployment changes."""
    service = instance.service
    if not service:
        return

    # Emit Prometheus metric for the deployment outcome.
    try:
        from config.metrics import (
            DEPLOYMENT_DURATION,
            SERVICE_BUILDS_TOTAL,
            SERVICE_DEPLOYMENTS_TOTAL,
            SERVICES_ACTIVE,
        )
        SERVICE_DEPLOYMENTS_TOTAL.labels(
            service_id=str(service.id),
            status=instance.status,
        ).inc()
        if instance.status in (Deployment.Status.ACTIVE, Deployment.Status.FAILED):
            result = 'success' if instance.status == Deployment.Status.ACTIVE else 'failure'
            SERVICE_BUILDS_TOTAL.labels(result=result).inc()
            if instance.duration_seconds and instance.duration_seconds > 0:
                DEPLOYMENT_DURATION.observe(float(instance.duration_seconds))
        SERVICES_ACTIVE.set(
            Service.objects.filter(status=Service.Status.ACTIVE).count()
        )
    except Exception as exc:  # never let metrics break the request path
        logging.getLogger(__name__).debug("smsly metric emission failed: %s", exc)

    # Get the latest deployment for this service
    latest_deployment = service.deployments.order_by('-created_at').first()
    
    # Determine service status based on latest deployment
    if latest_deployment:
        if latest_deployment.status == Deployment.Status.ACTIVE:
            new_status = Service.Status.ACTIVE
        elif latest_deployment.status == Deployment.Status.FAILED:
            new_status = Service.Status.ACTIVE  # Service remains active even if deployment fails
        elif latest_deployment.status in [
            Deployment.Status.QUEUED,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.REVIEW,
            Deployment.Status.HEALTH_CHECK,
            Deployment.Status.TRAFFIC_SHIFTING,
        ]:
            new_status = Service.Status.ACTIVE  # Service is active during deployment
        else:
            new_status = service.status  # Keep current status
    else:
        new_status = Service.Status.ACTIVE  # Service remains active without deployments
    
    # Update service status if it changed
    if service.status != new_status:
        old_status = service.status
        service.status = new_status
        service.save(update_fields=['status'])
        
        # Broadcast the status change via WebSocket
        channel_layer = get_channel_layer()
        if channel_layer:
            try:
                async_to_sync(channel_layer.group_send)(
                    f"user_services_{service.owner.id}",
                    {
                        'type': 'service_status_update',
                        'service_id': str(service.id),
                        'service_name': service.name,
                        'status': new_status,
                        'deployment_status': latest_deployment.status if latest_deployment else 'unknown',
                        'updated_at': service.updated_at.isoformat(),
                    }
                )
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "Failed to broadcast service status update for %s: %s", service.id, e
                )
        
        # Log the status change
        log_event(
            actor=service.owner.get_username() if service.owner else 'system',
            action='SERVICE_STATUS_CHANGE',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'old_status': old_status,
                'new_status': new_status,
                'deployment_id': str(latest_deployment.id) if latest_deployment else None,
                'deployment_status': latest_deployment.status if latest_deployment else None,
            },
        )


@receiver(post_save, sender=Service)
def broadcast_service_status_change(sender, instance, created, **kwargs):
    """Broadcast service status changes via WebSocket."""
    if not created:
        try:
            from config.metrics import SERVICES_ACTIVE
            SERVICES_ACTIVE.set(
                Service.objects.filter(status=Service.Status.ACTIVE).count()
            )
        except Exception as exc:
            logging.getLogger(__name__).debug("smsly_services_active update failed: %s", exc)

    if created:
        return  # Skip creation - handled by other signals

    # Only broadcast if status actually changed
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return

    # Get the latest deployment for this service
    latest_deployment = instance.deployments.order_by('-created_at').first()
    
    # Broadcast the status change
    channel_layer = get_channel_layer()
    if channel_layer and instance.owner:
        try:
            async_to_sync(channel_layer.group_send)(
                f"user_services_{instance.owner.id}",
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


@receiver(post_save, sender=Deployment)
def notify_deployment_lifecycle(sender, instance, created, **kwargs):
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

        # 2. Sync domain to .env so future --update runs pick up correct values.
        #    The host .env is bind-mounted at /app/.env (rw) but the container
        #    user (smsly, UID 1000) may not have write permission if the host
        #    file is owned by root.  We try multiple paths and log clearly.
        _new_domain = (instance.domain or "").strip()
        _new_ssl = instance.use_ssl
        _new_scheme = 'https' if _new_ssl else 'http'
        _new_origin = f'{_new_scheme}://{_new_domain}' if _new_domain else ''

        # Env vars to sync (value providers mapped to (line_prefix, value_or_none))
        # When value is None, the existing line is preserved as-is (not synced).
        # ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, and CORS_ALLOWED_ORIGINS are
        # handled at runtime by patch_runtime_settings and should NOT be
        # overwritten — preserving avoids stripping IP addresses from .env.
        _env_sync_map = {
            'DOMAIN=': _new_domain,
            'USE_SSL=': 'true' if _new_ssl else 'false',
            'SITE_URL=': _new_origin or None,
            'ALLOWED_HOSTS=': None,
            'CSRF_TRUSTED_ORIGINS=': None,
            'CORS_ALLOWED_ORIGINS=': None,
        }

        for _env_path in ("/app/.env", "/caddy-config/.env"):
            if not (_new_domain and os.path.isfile(_env_path)):
                continue
            try:
                _updated = False
                _lines = []
                with open(_env_path, "r", encoding="utf-8") as _fh:
                    for _line in _fh:
                        _matched = False
                        for _key, _val in _env_sync_map.items():
                            if _line.startswith(_key):
                                if _val is not None:
                                    _lines.append(f"{_key}{_val}\n")
                                    _updated = True
                                else:
                                    # Preserve original line for None-valued keys
                                    _lines.append(_line)
                                _matched = True
                                break
                        if not _matched:
                            _lines.append(_line)
                for _key, _val in _env_sync_map.items():
                    if _val is not None and not any(l.startswith(_key) for l in _lines):
                        _lines.append(f"{_key}{_val}\n")
                        _updated = True
                if _updated:
                    # Direct write instead of atomic rename.
                    # os.replace() (rename across filesystems) fails with
                    # "Device or resource busy" on Docker bind mounts because
                    # the .env is mounted into multiple containers simul-
                    # taneously.  Direct write keeps all containers consistent.
                    with open(_env_path, "w", encoding="utf-8") as _fh:
                        _fh.writelines(_lines)
                    logger.info(
                        "Synced %s: DOMAIN=%s, USE_SSL=%s", _env_path, _new_domain, _new_ssl
                    )
            except PermissionError:
                logger.warning(
                    "Cannot write to %s (Permission denied). "
                    "Fix with: sudo chown 1000:1000 %s && sudo chmod 664 %s",
                    _env_path, _env_path, _env_path,
                )
            except OSError as _exc:
                logger.error("Failed to sync %s: %s", _env_path, _exc)

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


@receiver(post_save, sender=Deployment)
def sync_preview_status_on_deployment_change(sender, instance, created, **kwargs):
    """Update PreviewEnvironment status when the transient service's deployment changes."""
    logger = logging.getLogger(__name__)
    service = instance.service
    if not service or not service.is_preview:
        return

    # Find the corresponding PreviewEnvironment
    if not service.name.startswith("preview-"):
        return

    try:
        preview_id_prefix = service.name.split("-")[-1]
        from apps.deployments.models_safedeploy import PreviewEnvironment
        # Find the PreviewEnvironment for this parent service matching the unique hex prefix
        parent_service = service.parent_service
        previews = PreviewEnvironment.objects.filter(service=parent_service)
        preview = None
        for p in previews:
            if p.id.hex.startswith(preview_id_prefix):
                preview = p
                break

        if not preview:
            logger.warning(
                "Could not find PreviewEnvironment for transient service %s",
                service.name
            )
            return

        # Map Deployment status to PreviewEnvironment status
        old_status = preview.status
        new_status = None
        error_msg = ""

        if instance.status == Deployment.Status.QUEUED:
            new_status = PreviewEnvironment.Status.BUILDING
        elif instance.status == Deployment.Status.BUILDING:
            new_status = PreviewEnvironment.Status.BUILDING
        elif instance.status == Deployment.Status.BUILD_FAILED:
            new_status = PreviewEnvironment.Status.BUILD_FAILED
            error_msg = instance.ai_diagnosis or "Build failed"
        elif instance.status in [Deployment.Status.DEPLOYING, Deployment.Status.HEALTH_CHECK]:
            new_status = PreviewEnvironment.Status.HEALTH_CHECK_RUNNING
        elif instance.status == Deployment.Status.ACTIVE:
            new_status = PreviewEnvironment.Status.READY
        elif instance.status in [Deployment.Status.FAILED, Deployment.Status.CANCELLED]:
            new_status = PreviewEnvironment.Status.HEALTH_CHECK_FAILED
            error_msg = instance.ai_diagnosis or f"Deployment {instance.status.lower()}"

        if new_status and old_status != new_status:
            preview.status = new_status
            if error_msg:
                preview.error_message = error_msg
            preview.save(update_fields=['status', 'error_message', 'updated_at'])
            logger.info(
                "Synced PreviewEnvironment %s status from %s to %s via deployment %s",
                preview.id, old_status, new_status, instance.id
            )
            
            # If the preview transitioned to READY, ensure Caddy is updated
            if new_status == PreviewEnvironment.Status.READY:
                try:
                    from apps.deployments.tasks import _regenerate_caddyfile
                    _regenerate_caddyfile()
                except Exception as caddy_exc:
                    logger.warning("Failed to regenerate Caddyfile on preview ready: %s", caddy_exc)

    except Exception as e:
        logger.error(
            "Failed to sync preview status for deployment %s: %s",
            instance.id, e, exc_info=True
        )


@receiver(post_delete, sender=Service)
def regenerate_caddyfile_on_service_deletion(sender, instance, **kwargs):
    """Regenerate Caddyfile when a service is deleted to clean up routes."""
    logger = logging.getLogger(__name__)
    try:
        from apps.deployments.tasks import _regenerate_caddyfile
        _regenerate_caddyfile()
        logger.info("Caddyfile regenerated after service %s deletion", instance.name)
    except Exception as exc:
        logger.warning("Could not regenerate Caddyfile after service deletion: %s", exc)

