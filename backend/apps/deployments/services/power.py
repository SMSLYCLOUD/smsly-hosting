"""Bulk + single power operations for services (stop/start/restart).

Single-service views delegate here so bulk endpoints and the auto-off
timer share one implementation. All functions are defensive: per-service
failures are returned as dicts, never raised — callers decide whether a
partial failure is an error (single view) or a summary line (bulk).
"""
import logging
import subprocess
import time

logger = logging.getLogger(__name__)

_PLATFORM_INFRA_PREFIXES = (
    "smsly-hosting-",
    "smsly-addon-",
    "envoy-",
    "smsly-postgres",
    "smsly-shared-postgres",
    "smsly-redis",
    "smsly-appsec",
    "smsly-spire",
    "smsly-loki",
    "smsly-sablier",
    "smsly-infisical",
    "smsly-patroni",
    "smsly-etcd",
    "smsly-docker-",
    "smsly-postgres-exporter",
)

_PLATFORM_INFRA_EXACT = frozenset({
    "smsly-falco",
    "smsly-crowdsec",
    "smsly-cloudflare-bouncer",
    "smsly-promtail",
    "smsly-prometheus",
    "smsly-cadvisor",
    "smsly-node-exporter",
    "smsly-alertmanager",
    "smsly-pgbackrest",
    "smsly-mcp-server",
    "smsly-grafana",
    "smsly-haproxy",
})


def is_platform_or_infra_container(name: str) -> bool:
    """True for platform, infra, monitoring, WAF, SPIRE, or addon containers."""
    clean = (name or "").strip().lstrip("/").lower()
    if not clean:
        return False
    return clean in _PLATFORM_INFRA_EXACT or clean.startswith(_PLATFORM_INFRA_PREFIXES)


def _docker_restart_arg(policy_name: str) -> str:
    """Normalize Service.restart_policy for `docker update --restart=...`."""
    policy = (policy_name or "").strip().lower()
    if policy == "on-failure":
        return "on-failure:5"
    if policy in ("unless-stopped", "always", "no"):
        return policy
    return "unless-stopped"


def restore_restart_policy(container_id: str, policy_name: str = "unless-stopped") -> bool:
    """Best-effort restore of a container's Docker restart policy."""
    cid = (container_id or "").strip()
    if not cid:
        return False
    arg = _docker_restart_arg(policy_name)
    try:
        res = subprocess.run(
            ["docker", "update", f"--restart={arg}", cid],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if res.returncode != 0:
            logger.debug("docker update --restart=%s %s failed: %s", arg, cid, (res.stderr or "").strip())
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("restore_restart_policy skipped for %s: %s", cid, exc)
        return False


def quarantine_crash_loop_service(service, container_ref: str, reason: str, exit_code=None) -> dict:
    """Stop a crash-looping tenant container, disarm Docker auto-restart, and
    mark the service ``needs_manual_intervention``.
    """
    from apps.deployments.models import Deployment

    svc_name = getattr(service, "name", "") or ""
    if is_platform_or_infra_container(svc_name) or is_platform_or_infra_container(container_ref or ""):
        return {"ok": False, "skipped": True, "quarantined": False, "reason": "platform_or_infra"}

    in_flight = [
        Deployment.Status.QUEUED,
        Deployment.Status.REVIEW,
        Deployment.Status.BUILDING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.HEALTH_CHECK,
    ]
    if Deployment.objects.filter(service=service, status__in=in_flight).exists():
        return {"ok": False, "skipped": True, "quarantined": False, "reason": "deployment_in_flight"}

    try:
        from apps.core.services.health_monitor import _save_container_logs_to_deployment
        _save_container_logs_to_deployment(service, exit_code=exit_code)
    except Exception as exc:
        logger.debug("Pre-quarantine log capture failed for %s: %s", svc_name, exc)

    ref = (container_ref or "").strip()
    stopped = False
    if ref:
        try:
            subprocess.run(
                ["docker", "update", "--restart=no", ref],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            stop_res = subprocess.run(
                ["docker", "stop", "--timeout", "10", ref],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            stopped = stop_res.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("Docker quarantine stop failed for %s (%s): %s", svc_name, ref, exc)

    if getattr(service, "health_status", "") != "needs_manual_intervention":
        service.health_status = "needs_manual_intervention"
        service.save(update_fields=["health_status", "updated_at"])

    _audit(
        "system",
        "CONTAINER_CRASH_LOOP_QUARANTINED",
        service,
        {
            "container": ref,
            "reason": reason,
            "exit_code": exit_code,
            "stopped": stopped,
        },
    )

    try:
        from apps.core.services.health_monitor import _dispatch_health_alert
        _dispatch_health_alert(
            service,
            "needs_manual_intervention",
            f"Auto-quarantined crash-looping container: {reason}",
            exit_code=exit_code,
        )
    except Exception as exc:
        logger.debug("Quarantine alert dispatch failed for %s: %s", svc_name, exc)

    return {"ok": True, "quarantined": True, "stopped": stopped, "container": ref}


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
        from apps.core.crypto_daemon import cryptd_client, is_service_encryption_enabled
        if cryptd_client.is_enabled() and is_service_encryption_enabled(service):
            cryptd_client.lock_service(str(service.id), reason=f"service stopped ({actor})")
    except Exception as exc:
        logger.debug("cryptd lock_service skipped on stop for %s: %s", service.id, exc)
    try:
        from apps.core.services.health_monitor import reset_restart_state
        reset_restart_state(str(service.id))
    except Exception:
        pass
    _audit(actor, 'SERVICE_STOP', service,
           {'deployments_cancelled': count, 'method': method, 'container_id': container_id})
    if bool(getattr(service, 'sablier_enabled', False)):
        try:
            from apps.deployments.tasks.deploy.caddy import _regenerate_caddyfile
            _regenerate_caddyfile()
        except Exception as exc:
            logger.debug("Caddy sync skipped on Sablier stop for %s: %s", service.name, exc)
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
        from apps.core.crypto_daemon import (
            cryptd_client,
            is_service_encryption_enabled,
            register_and_unlock_service,
        )
        if cryptd_client.is_enabled() and is_service_encryption_enabled(service):
            register_and_unlock_service(service, unlock_now=True)
    except Exception as exc:
        logger.error("cryptd unlock failed before starting %s: %s", service.name, exc)
        return {'ok': False, 'error': f'Encryption controller refused start (locked): {exc}'}
    try:
        restore_restart_policy(
            container_id,
            getattr(service, "restart_policy", "unless-stopped") or "unless-stopped",
        )
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
    if bool(getattr(service, 'sablier_enabled', False)):
        try:
            from apps.deployments.tasks.deploy.caddy import _regenerate_caddyfile
            _regenerate_caddyfile()
        except Exception as exc:
            logger.debug("Caddy sync skipped on Sablier start for %s: %s", service.name, exc)
    return {'ok': True, 'method': 'docker_start', 'container_id': container_id}


def restart_service(service, actor="system"):
    """Fast docker restart of the ACTIVE row's container."""
    container_id = _active_container_id(service)
    if not container_id:
        return {'ok': False, 'error': 'No active deployment to restart.'}
    try:
        from apps.core.crypto_daemon import (
            cryptd_client,
            is_service_encryption_enabled,
            register_and_unlock_service,
        )
        if cryptd_client.is_enabled() and is_service_encryption_enabled(service):
            register_and_unlock_service(service, unlock_now=True)
    except Exception as exc:
        logger.error("cryptd unlock failed before restarting %s: %s", service.name, exc)
        return {'ok': False, 'error': f'Encryption controller refused restart (locked): {exc}'}
    try:
        restore_restart_policy(
            container_id,
            getattr(service, "restart_policy", "unless-stopped") or "unless-stopped",
        )
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
        from apps.core.services.health_monitor import reset_restart_state
        reset_restart_state(str(service.id))
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
