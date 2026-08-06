import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models.signals import post_save
from django.dispatch import receiver

from ..models import Deployment, Service
from ..utils import log_event

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Deployment)
def sync_service_status_on_deployment_change(sender, instance, created, **kwargs):
    service = instance.service
    if not service:
        return

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
    except Exception as exc:
        logging.getLogger(__name__).debug("smsly metric emission failed: %s", exc)

    # The deletion lifecycle owns Service.status — never let a stray
    # Deployment save flip a service out of (or past) a deletion state,
    # or recover_stalled_deletions will never find it again.
    if service.status in (
        Service.Status.DELETION_PENDING,
        Service.Status.DELETION_FAILED,
        Service.Status.DELETED,
    ):
        return

    # Status recompute + broadcast + audit on every save is expensive;
    # only run when the deployment's status field changed.
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return

    latest_deployment = service.deployments.order_by('-created_at').first()

    if latest_deployment:
        if latest_deployment.status == Deployment.Status.ACTIVE:
            new_status = Service.Status.ACTIVE
        elif latest_deployment.status == Deployment.Status.FAILED:
            # A failed deploy does not prove the service is serving —
            # don't fabricate ACTIVE.
            new_status = Service.Status.UNKNOWN
        elif latest_deployment.status in [
            Deployment.Status.QUEUED,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.REVIEW,
            Deployment.Status.HEALTH_CHECK,
        ]:
            # In-flight deploys leave the currently-running version up.
            new_status = service.status
        else:
            new_status = service.status
    else:
        new_status = Service.Status.ACTIVE

    if service.status != new_status:
        old_status = service.status
        service.status = new_status
        service.save(update_fields=['status'])

        channel_layer = get_channel_layer()
        if channel_layer:
            user_ids = {service.owner_id}
            if service.project_id and service.project.team_id:
                from apps.teams.models import TeamMember
                member_ids = TeamMember.objects.filter(
                    team_id=service.project.team_id,
                ).values_list('user_id', flat=True)
                user_ids.update(member_ids)
            try:
                for uid in user_ids:
                    async_to_sync(channel_layer.group_send)(
                        f"user_services_{uid}",
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


@receiver(post_save, sender=Deployment)
def notify_deployment_lifecycle(sender, instance, created, **kwargs):
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

    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'status' not in update_fields:
        return

    if instance.status not in (
        Deployment.Status.ACTIVE,
        Deployment.Status.FAILED,
        Deployment.Status.CANCELLED,
    ):
        return

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
        import logging
        logging.getLogger(__name__).warning(
            "Failed to queue deploy notification for user %s: %s", owner.pk, exc
        )


# TODO: Calls _regenerate_caddyfile() synchronously. Consider dispatching
# to a Celery task to avoid blocking the request thread.
@receiver(post_save, sender=Deployment)
def sync_preview_status_on_deployment_change(sender, instance, created, **kwargs):
    logger = logging.getLogger(__name__)
    service = instance.service
    if not service or not service.is_preview:
        return

    if not service.name.startswith("preview-"):
        return

    try:
        preview_id_prefix = service.name.split("-")[-1]
        from apps.deployments.models.safedeploy import PreviewEnvironment
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

        old_status = preview.status
        new_status = None
        error_msg = ""

        if instance.status in (Deployment.Status.QUEUED, Deployment.Status.BUILDING):
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

            if new_status == PreviewEnvironment.Status.READY:
                try:
                    from apps.deployments.tasks.deploy.helpers import _regenerate_caddyfile
                    _regenerate_caddyfile()
                except Exception as caddy_exc:
                    logger.warning("Failed to regenerate Caddyfile on preview ready: %s", caddy_exc)

    except Exception as e:
        logger.error(
            "Failed to sync preview status for deployment %s: %s",
            instance.id, e, exc_info=True
        )
