"""Bulk + single power operations for services (stop/start/restart).

Single-service views delegate here so bulk endpoints and the auto-off
timer share one implementation. All functions are defensive: per-service
failures are returned as dicts, never raised — callers decide whether a
partial failure is an error (single view) or a summary line (bulk).
"""
import logging
import time

logger = logging.getLogger(__name__)


def _active_container_id(service):
    """Container id of the ACTIVE deployment row, or None."""
    from apps.deployments.models import Deployment
    row = service.deployments.filter(
        status=Deployment.Status.ACTIVE).order_by('-created_at').first()
    return row.container_id if row else None


def _resolve_local(service):
    """Return (target_type, active_server), defaulting to local on error."""
    try:
        from apps.deployments.utils.target import resolve_active_execution_target
        target = resolve_active_execution_target(service)
        return target.get("target_type", "local"), target.get("server_obj")
    except Exception as exc:
        logger.debug("Power target resolution failed for %s, assuming local: %s", service.id, exc)
        return "local", None


def _audit(actor, action, service, metadata):
    try:
        from apps.deployments.models.audit import AuditLog
        AuditLog(
            actor=actor, action=action, target=f'Service: {service.name}',
            metadata={'service_id': str(service.id), **metadata},
        ).save()
    except Exception as exc:
        logger.debug("Power audit log failed: %s", exc)


def stop_service(service, actor="system"):
    """Docker-stop the active container, cancel in-flight rows, mark STOPPED."""
    from django.utils import timezone
    from apps.deployments.models import Deployment
    from apps.deployments.models import Service

    if service.status == Service.Status.STOPPED:
        return {'ok': True, 'method': 'already_stopped', 'deployments_cancelled': 0}
    count = service.deployments.filter(
        status__in=[
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
            Deployment.Status.QUEUED,
            Deployment.Status.REVIEW,
        ]
    ).update(status=Deployment.Status.CANCELLED, finished_at=timezone.now())

    method, container_id = 'deployment_cancel_only', None
    try:
        container_id = _active_container_id(service)
        if container_id:
            from apps.deployments.services.container_runtime import ContainerRuntime
            ContainerRuntime().stop_container(container_id)
            method = 'docker_stop'
    except Exception as exc:
        logger.warning("Docker stop failed for %s: %s", service.name, exc)
        method = 'docker_stop_failed'

    service.status = Service.Status.STOPPED
    service.health_status = 'unknown'
    service.save(update_fields=['status', 'health_status', 'updated_at'])
    try:
        from apps.core.services.health_monitor import reset_restart_state
        reset_restart_state(str(service.id))
    except Exception:
        pass
    _audit(actor, 'SERVICE_STOP', service,
           {'deployments_cancelled': count, 'method': method, 'container_id': container_id})
    return {'ok': True, 'method': method, 'deployments_cancelled': count,
            'container_id': container_id}


def start_service(service, actor="system"):
    """Docker-start the ACTIVE row's container, mark ACTIVE/starting."""
    from apps.deployments.models import Service

    if service.status != Service.Status.STOPPED:
        return {'ok': False, 'error': 'Only stopped services can be started.'}
    container_id = _active_container_id(service)
    if not container_id:
        return {'ok': False, 'error': 'No active deployment to start.'}
    try:
        from apps.deployments.services.container_runtime import ContainerRuntime
        ContainerRuntime().start_container(container_id)
    except Exception as exc:
        logger.error("Docker start failed for %s: %s", service.name, exc)
        return {'ok': False, 'error': 'Docker start failed.'}
    service.status = Service.Status.ACTIVE
    service.health_status = 'starting'
    service.save(update_fields=['status', 'health_status', 'updated_at'])
    try:
        from apps.core.services.health_monitor import reset_restart_state
        reset_restart_state(str(service.id))
        from django.core.cache import cache
        cache.set(f"health:restart_grace:{service.id}", True, timeout=60)
    except Exception:
        pass
    _audit(actor, 'SERVICE_START', service,
           {'container_id': container_id, 'method': 'docker_start'})
    return {'ok': True, 'method': 'docker_start', 'container_id': container_id}


def restart_service(service, actor="system"):
    """Fast docker restart of the ACTIVE row's container."""
    container_id = _active_container_id(service)
    if not container_id:
        return {'ok': False, 'error': 'No active deployment to restart.'}
    try:
        from apps.deployments.services.container_runtime import ContainerRuntime
        ContainerRuntime().restart_container(container_id)
    except Exception as exc:
        return {'ok': False, 'error': f'Docker restart failed: {exc}'}
    service.health_status = 'starting'
    fields = ['health_status', 'updated_at']
    if service.status == 'STOPPED':
        from apps.deployments.models import Service as ServiceModel
        service.status = ServiceModel.Status.ACTIVE
        fields = ['health_status', 'status', 'updated_at']
    service.save(update_fields=fields)
    try:
        from django.core.cache import cache
        cache.set(f"health:restart_grace:{service.id}", True, timeout=60)
    except Exception:
        pass
    _audit(actor, 'SERVICE_FAST_RESTART', service,
           {'container_id': container_id, 'method': 'docker_restart'})
    return {'ok': True, 'method': 'docker_restart', 'container_id': container_id}


def _iter_power_targets(only_status=None):
    from apps.deployments.models import Service
    qs = Service.objects.all().only('id', 'name', 'status')
    if only_status:
        qs = qs.filter(status=only_status)
    return qs.order_by('name')


def _check_write(user, service) -> bool:
    try:
        from apps.teams.permissions import assert_can_write
        assert_can_write(user, service)
        return True
    except Exception:
        return False


def power_all(op, actor, user=None, stagger_seconds=0):
    """Run op (stop/start/restart) over services sequentially.

    stop/start operate on status sets (ACTIVE for stop, STOPPED for start);
    restart operates on ACTIVE. Returns a summary dict; per-service errors
    are collected, never raised. Restarts stagger to avoid thundering herd.
    """
    from apps.deployments.models import Service
    fn = {'stop': stop_service, 'start': start_service, 'restart': restart_service}[op]
    if op == 'stop':
        targets = list(_iter_power_targets(only_status=Service.Status.ACTIVE))
    elif op == 'start':
        targets = list(_iter_power_targets(only_status=Service.Status.STOPPED))
    else:
        targets = list(_iter_power_targets(only_status=Service.Status.ACTIVE))
    done, failed, skipped = [], {}, []
    for service in targets:
        if user is not None and not _check_write(user, service):
            skipped.append(service.name)
            continue
        try:
            result = fn(service, actor=actor)
            if result.get('ok'):
                done.append(service.name)
            else:
                failed[service.name] = result.get('error', 'failed')
        except Exception as exc:  # never let one service break the sweep
            failed[service.name] = str(exc)[:200]
        if stagger_seconds and op == 'restart':
            time.sleep(stagger_seconds)
    try:
        from apps.deployments.models.audit import AuditLog
        AuditLog(
            actor=actor, action=f'SERVICE_BULK_{op.upper()}',
            target=f'Bulk {op}: {len(done)} ok, {len(failed)} failed, {len(skipped)} skipped',
            metadata={'done': done[:50], 'failed': failed, 'skipped': skipped[:50]},
        ).save()
    except Exception as exc:
        logger.debug("Bulk power audit failed: %s", exc)
    return {'op': op, 'done': done, 'failed': failed, 'skipped': skipped,
            'done_count': len(done), 'failed_count': len(failed), 'skipped_count': len(skipped)}
