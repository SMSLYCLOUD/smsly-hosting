import ipaddress
import logging
import os
import re
import secrets

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from services.caddy_manager import apply_caddyfile, generate_caddyfile

from .models import (  # type: ignore[attr-defined]
    Deployment,
    EnvironmentVariable,
    ManagedServer,
    PlatformConfig,
    Service,
)

logger = logging.getLogger(__name__)
from .models_addons import Addon
from .models_backup import BackupSchedule
from .models_cron import CronJob
from .models_storage import Volume
from .utils import log_event


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


@receiver(post_save, sender=Service)
def regenerate_caddyfile_on_service_change(sender, instance, created, **kwargs):
    """Regenerate the Caddyfile when a service is created or its
    public_domain changes. This ensures new services get Caddy site blocks
    (and SSL certs) without requiring a successful deployment — previously
    the regeneration only happened in the deployment task, so services
    whose deployment failed (e.g. GitHub webhook setup error) never got
    routed and returned 404 on their wildcard subdomain.
    """
    # On created, the public_domain is already set (auto-generated during
    # the deploy request). On update, only regenerate if public_domain
    # actually changed (to avoid noise on every save).
    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'public_domain' not in update_fields and 'custom_domains' not in update_fields:
        return
    try:
        from apps.deployments.tasks_caddy import _regenerate_caddyfile
        _regenerate_caddyfile()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not regenerate Caddyfile from Service signal: %s", exc)


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
            # Broadcast to the owner AND all team members so they
            # receive real-time updates for services they can manage.
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

    # Broadcast the status change to owner + team members.
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
        _new_grafana_url = f'{_new_origin}/grafana' if _new_domain else None

        # Env vars to sync (value providers mapped to (line_prefix, value_or_none))
        # When value is None, the existing line is preserved as-is (not synced).
        # ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, and CORS_ALLOWED_ORIGINS are
        # handled at runtime by patch_runtime_settings and should NOT be
        # overwritten — preserving avoids stripping IP addresses from .env.
        _env_sync_map = {
            'DOMAIN=': _new_domain,
            'USE_SSL=': 'true' if _new_ssl else 'false',
            'SITE_URL=': _new_origin or None,
            'GRAFANA_EXTERNAL_URL=': _new_grafana_url,
            'ALLOWED_HOSTS=': None,
            'CSRF_TRUSTED_ORIGINS=': None,
            'CORS_ALLOWED_ORIGINS=': None,
        }

        for _env_path in ("/app/.env", "/caddy-config/.env"):
            if not (_new_domain and os.path.isfile(_env_path)):
                continue
            # Skip if file is not writable (e.g. read-only bind mount)
            if not os.access(_env_path, os.W_OK):
                logger.debug("Skipping .env sync: %s is not writable", _env_path)
                continue
            try:
                _updated = False
                _lines = []
                with open(_env_path, encoding="utf-8") as _fh:
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
                    if _val is not None and not any(line.startswith(_key) for line in _lines):
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

            # If the preview transitioned to READY, ensure Caddy is updated
            if new_status == PreviewEnvironment.Status.READY:
                try:
                    from apps.deployments.tasks_caddy import _regenerate_caddyfile
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
        from apps.deployments.tasks_caddy import _regenerate_caddyfile
        _regenerate_caddyfile()
        logger.info("Caddyfile regenerated after service %s deletion", instance.name)
    except Exception as exc:
        logger.warning("Could not regenerate Caddyfile after service deletion: %s", exc)


@receiver(pre_delete, sender=Service)
def remove_service_docker_volumes_on_delete(sender, instance, **kwargs):
    log = logging.getLogger(__name__)
    volumes = list(instance.volumes.all())
    if not volumes:
        return
    try:
        import docker
        client = docker.from_env()
    except Exception as exc:
        log.warning("Docker SDK unavailable on service delete: %s", exc)
        return
    for vol in volumes:
        try:
            docker_vol = client.volumes.get(vol.name)
            docker_vol.remove(force=True)
            log.info("Removed docker volume %s for service %s", vol.name, instance.name)
        except Exception as exc:
            log.debug("Failed to remove docker volume %s: %s", vol.name, exc)


_VOLUME_MOUNT_PATH_ALLOWED_PREFIXES = (
    "/var/lib/smsly/volumes/",
    "/data/",
    "/opt/smsly/data/",
    "/srv/",
    "/storage/",
    "/workspace/",
    "/home/smsly/",
    "/mnt/",
    "/opt/app/",
)


@receiver(pre_save, sender=Volume)
def validate_volume_name_pre_save(sender, instance, **kwargs):
    """SECURITY (Issue 140): defence-in-depth for Volume.name.

    The serializer runs ``_validate_volume_name`` first, but admin
    scripts or direct ORM writes can bypass it.  The model-level
    ``clean()`` is not invoked automatically by ``save()``, so we
    attach a ``pre_save`` signal that enforces the same
    ``^[a-zA-Z0-9_-]{1,64}$`` regex.
    """
    if instance.name is None:
        return
    name = str(instance.name)
    if not Volume._VOLUME_NAME_RE.match(name):
        raise ValidationError({
            "name": (
                "name must match ^[a-zA-Z0-9_-]{1,64}$ "
                "(letters, digits, underscore or hyphen; max 64 chars)."
            )
        })


@receiver(pre_save, sender=Volume)
def validate_volume_mount_path_pre_save(sender, instance, **kwargs):
    mount = getattr(instance, "mount_path", None)
    if mount is None:
        return
    if not isinstance(mount, str) or not mount:
        raise ValidationError({"mount_path": "mount_path is required."})
    if not any(mount == prefix.rstrip("/") or mount.startswith(prefix)
               for prefix in _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES):
        raise ValidationError({
            "mount_path": (
                "mount_path must start with one of "
                f"{', '.join(_VOLUME_MOUNT_PATH_ALLOWED_PREFIXES)}."
            )
        })


_MANAGED_SERVER_HOST_RE = re.compile(r"^[a-zA-Z0-9.\-]+$")

_RFC1918_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def _is_private_or_internal_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    return bool(any(ip in net for net in _RFC1918_RANGES))


@receiver(pre_save, sender=ManagedServer)
def validate_managed_server_host_pre_save(sender, instance, **kwargs):
    host = getattr(instance, "host", None)
    if host is None:
        return
    if not isinstance(host, str) or not host:
        raise ValidationError({"host": "host is required."})
    if not _MANAGED_SERVER_HOST_RE.match(host):
        raise ValidationError({
            "host": (
                "host must match ^[a-zA-Z0-9.-]+$ (letters, digits, "
                "dot and dash only)."
            )
        })
    if _is_private_or_internal_ip(host):
        raise ValidationError({
            "host": (
                f"host {host!r} is a loopback, link-local, RFC1918, "
                "multicast, or unspecified address."
            )
        })


_CRON_FIELD_RE = re.compile(r"^[\d*/,\-\s]+$")
_CRON_FIELD_COUNT = 5
_CRON_MIN_GAP_SECONDS = 300


def _parse_cron_field(field: str, lo: int, hi: int) -> list[int]:
    parts: list[int] = []
    for piece in field.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if piece == "*":
            return list(range(lo, hi + 1))
        if piece.startswith("*/"):
            step_str = piece[2:]
            if not step_str.isdigit():
                raise ValueError(f"invalid step in {field!r}")
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"step must be positive in {field!r}")
            return list(range(lo, hi + 1, step))
        if "-" in piece:
            lo_s, hi_s = piece.split("-", 1)
            if not (lo_s.isdigit() and hi_s.isdigit()):
                raise ValueError(f"invalid range in {field!r}")
            a, b = int(lo_s), int(hi_s)
            if a > b:
                a, b = b, a
            parts.extend(range(a, b + 1))
            continue
        if not piece.isdigit():
            raise ValueError(f"invalid token in {field!r}")
        parts.append(int(piece))
    return parts


def _smallest_gap(values: list[int], modulus: int) -> int:
    if not values:
        return modulus
    sorted_vals = sorted(set(values))
    gaps = []
    for idx, val in enumerate(sorted_vals):
        nxt = sorted_vals[(idx + 1) % len(sorted_vals)]
        gap = (nxt - val) % modulus
        if gap == 0:
            gap = modulus
        gaps.append(gap)
    return min(gaps)


def _cron_minute_gap(minute_field: str) -> int:
    minutes = _parse_cron_field(minute_field, 0, 59)
    return _smallest_gap(minutes, 60) * 60


@receiver(pre_save, sender=CronJob)
def validate_cron_schedule_pre_save(sender, instance, **kwargs):
    schedule = getattr(instance, "schedule", None)
    if schedule is None or not isinstance(schedule, str):
        return
    schedule = schedule.strip()
    if not schedule:
        raise ValidationError({"schedule": "schedule is required."})
    fields = schedule.split()
    if len(fields) != _CRON_FIELD_COUNT:
        raise ValidationError({
            "schedule": (
                f"cron expression must have exactly {_CRON_FIELD_COUNT} fields; "
                f"got {len(fields)}."
            )
        })
    for field in fields:
        if not _CRON_FIELD_RE.match(field):
            raise ValidationError({
                "schedule": (
                    f"invalid characters in cron field {field!r}; "
                    "only digits, '*', '/', ',', '-' and whitespace are allowed."
                )
            })
    try:
        minute_gap = _cron_minute_gap(fields[0])
    except ValueError as exc:
        raise ValidationError({"schedule": f"invalid minute field: {exc}"})
    if minute_gap < _CRON_MIN_GAP_SECONDS:
        raise ValidationError({
            "schedule": (
                f"schedule fires more often than every "
                f"{_CRON_MIN_GAP_SECONDS // 60} minutes "
                f"(minimum gap detected: {minute_gap}s)."
            )
        })


@receiver(pre_save, sender=BackupSchedule)
def validate_backup_schedule_cron_pre_save(sender, instance, **kwargs):
    """Validate BackupSchedule.cron_expression — same rules as CronJob.schedule."""
    cron_expr = getattr(instance, "cron_expression", None)
    if cron_expr is None or not isinstance(cron_expr, str):
        return
    cron_expr = cron_expr.strip()
    if not cron_expr:
        raise ValidationError({"cron_expression": "cron_expression is required."})
    fields = cron_expr.split()
    if len(fields) != _CRON_FIELD_COUNT:
        raise ValidationError({
            "cron_expression": (
                f"cron expression must have exactly {_CRON_FIELD_COUNT} fields; "
                f"got {len(fields)}."
            )
        })
    for field in fields:
        if not _CRON_FIELD_RE.match(field):
            raise ValidationError({
                "cron_expression": (
                    f"invalid characters in cron field {field!r}; "
                    "only digits, '*', '/', ',', '-' and whitespace are allowed."
                )
            })


@receiver(pre_delete, sender=Addon)
def deprovision_addon_on_delete(sender, instance, **kwargs):
    """Ensure addon infrastructure is torn down before the row is removed.

    The cascade path used to drop the ``Addon`` row and leave the
    underlying Docker volume / Coolify resource behind. The fix
    enqueues the existing ``deprovision_addon_task`` so any
    container, volume and Coolify record associated with the addon
    is removed even when the addon is hard-deleted (not soft-marked).
    """
    log = logging.getLogger(__name__)
    try:
        from .tasks_addons import deprovision_addon_task
        try:
            deprovision_addon_task.delay(str(instance.id))
        except Exception:
            deprovision_addon_task(str(instance.id))
        log.info(
            "Dispatched deprovision_addon_task for addon %s on pre_delete",
            instance.id,
        )
    except Exception as exc:
        log.warning(
            "Failed to dispatch deprovision_addon_task for addon %s: %s",
            instance.id, exc,
        )
    try:
        volume_name = (
            f"smsly-addon-{instance.addon_type.lower()}-{instance.id}"
        )
        import docker
        client = docker.from_env()
        docker_vol = client.volumes.get(volume_name)
        docker_vol.remove(force=True)
        log.info("Removed addon docker volume %s on pre_delete", volume_name)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bundle cleanup on delete
# ---------------------------------------------------------------------------

from .models_bundles import Bundle


@receiver(pre_delete, sender=Bundle)
def deprovision_bundle_on_delete(sender, instance, **kwargs):
    """Ensure bundle infrastructure is torn down before the row is removed.

    Passes bundle name, service ID, and network name to the task so it can
    deprovision even after the Bundle row is deleted.
    """
    log = logging.getLogger(__name__)
    try:
        from .tasks_bundles import deprovision_bundle_task
        bundle_name = instance.name
        service_id = str(instance.service_id)
        network_name = instance.network or ''
        try:
            deprovision_bundle_task.delay(
                str(instance.id),
                bundle_name=bundle_name,
                service_id=service_id,
                network_name=network_name,
            )
        except Exception:
            deprovision_bundle_task(
                str(instance.id),
                bundle_name=bundle_name,
                service_id=service_id,
                network_name=network_name,
            )
        log.info(
            "Dispatched deprovision_bundle_task for bundle %s on pre_delete",
            instance.id,
        )
    except Exception as exc:
        log.warning(
            "Failed to dispatch deprovision_bundle_task for bundle %s: %s",
            instance.id, exc,
        )

