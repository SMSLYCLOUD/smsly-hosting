from __future__ import annotations

import logging
from datetime import timedelta

import docker
from celery import shared_task
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.cloud.services.compute import ComputeService
from apps.deployments.models import Deployment
from apps.deployments.constants import TASK_TIME_LIMIT_STANDARD
from apps.deployments.utils import (
    append_log,
    broadcast_status,
)

from .state import _mark_deployment_active, _post_deploy_success

logger = logging.getLogger(__name__)

def _get_config_hours(field: str, default: int) -> int:
    """Read deploy timing from PlatformConfig, falling back to default."""
    try:
        from apps.deployments.models import PlatformConfig
        return getattr(PlatformConfig.load(), field, default) or default
    except Exception:
        return default


def _do_promote(deployment: Deployment, provider: CloudProvider) -> None:
    service = deployment.service
    green_id = deployment.green_container_id
    if not green_id:
        raise RuntimeError("No green container ID on deployment — cannot promote")

    compute = ComputeService(provider)
    adapter = compute.adapter
    # Thread the service identity through: a fresh adapter has no
    # _service_id, so _resolve_network_name() falls back to plain
    # 'smsly-net' and the promoted canonical container loses its
    # project-scoped bridge (live incident: app on smsly-net while its
    # redis-shared addon alias lived on smsly-net-<project8> only).
    if hasattr(adapter, "_resolve_network_name"):
        try:
            adapter._service_id = str(service.id)
        except Exception:
            pass

    if not hasattr(adapter, 'promote_container'):
        target_type = "remote" if provider.provider_type == CloudProvider.ProviderType.REMOTE else "lite_agent"
        host_ip = "unknown"
        if getattr(provider, 'server', None):
            host_ip = provider.server.private_ip or provider.server.host

        _mark_deployment_active(deployment, target_type, host_ip, green_id)

        deployment.container_id = green_id
        deployment.status = Deployment.Status.ACTIVE
        deployment.finished_at = timezone.now()
        deployment.save()

        service.active_target_type = target_type
        service.active_host_ip = host_ip
        service.active_runtime_id = green_id
        service.save(update_fields=['active_target_type', 'active_host_ip', 'active_runtime_id'])

        broadcast_status(deployment)

        _post_deploy_success(deployment, service)
        return

    promoted_id = adapter.promote_container(service.name, green_id)

    # Belt-and-braces: the promoted canonical container must sit on the
    # service's scoped bridge even if the adapter resolved the fallback
    # network (fresh adapter, missing row at create time, drift, ...).
    # Non-fatal by design — a failed attach must never fail a promotion
    # whose container is otherwise healthy (the repair beat retries).
    try:
        from apps.deployments.services.network_scope import (
            attach_container_to_service_network,
        )
        attach_container_to_service_network(service, promoted_id)
    except Exception:
        logger.debug(
            "Post-promote scoped-network attach skipped for %s",
            service.name, exc_info=True,
        )

    _mark_deployment_active(deployment, "local", "127.0.0.1", promoted_id)

    deployment.container_id = promoted_id
    deployment.status = Deployment.Status.ACTIVE
    deployment.finished_at = timezone.now()
    deployment.save()

    service.active_target_type = "local"
    service.active_host_ip = "127.0.0.1"
    service.active_runtime_id = promoted_id
    service.save(update_fields=['active_target_type', 'active_host_ip', 'active_runtime_id'])

    broadcast_status(deployment)

    _post_deploy_success(deployment, service)
    append_log(
        deployment,
        f"[OK] Deployment promoted to ACTIVE. Container: {promoted_id}\n"
    )

    # After promote, the staged container is destroyed. Reset staging
    # verification status so the UI reflects there is no active staged
    # deployment.  Custom staging domains keep their DNS verification
    # (the CNAME is still valid), but auto-generated ones lose meaning.
    if service.staging_domain_verified:
        service.staging_domain_verified = False
        service.save(update_fields=['staging_domain_verified'])


    if provider.provider_type == CloudProvider.ProviderType.LOCAL:
        from .health import _local_route_timeout_seconds, _wait_for_local_route_ready
        route_timeout = _local_route_timeout_seconds(service)
        _wait_for_local_route_ready(
            deployment, service, timeout_seconds=route_timeout,
        )


@shared_task(
    name="apps.deployments.tasks.auto_promote_staged_deployments",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def auto_promote_staged_deployments():
    """Auto-promote deployments in STAGED status for longer than configured hours."""
    hours = _get_config_hours('auto_promote_hours', 12)
    if hours <= 0:
        return {'promoted': 0, 'skipped': 'disabled'}

    threshold = timezone.now() - timedelta(hours=hours)
    staged = Deployment.objects.filter(
        status=Deployment.Status.STAGED,
        staged_at__lte=threshold,
    ).select_related('service')

    promoted = 0
    skipped = 0
    for deployment in staged:
        try:
            from .provider import _resolve_provider_for_service
            provider = _resolve_provider_for_service(deployment.service, prefer_local=True)
            if not provider:
                logger.warning("Auto-promote: no provider for %s, skipping", deployment.service.name)
                continue
            from apps.deployments.services.safedeploy.promotion_guard import (
                check_promotion_readiness,
                format_readiness,
            )
            readiness = check_promotion_readiness(deployment, provider=provider)
            if not readiness['ready']:
                # Not ready is not failure: the row stays STAGED for the
                # next sweep (soak time, unhealthy green that may recover,
                # pending approval). Log once per sweep, don't fail the row.
                skipped += 1
                append_log(
                    deployment,
                    f"[AUTO-PROMOTE] {format_readiness(readiness)}\n"
                )
                continue
            for warning in readiness['warnings']:
                append_log(deployment, f"[AUTO-PROMOTE] Warning: {warning}\n")
            _do_promote(deployment, provider)
            append_log(
                deployment,
                f"[AUTO-PROMOTE] Deployment auto-promoted after {hours} hours.\n"
            )
            promoted += 1
        except Exception as exc:
            # A missing green container can never promote (it was GC'd or
            # crashed away). Fail the row fast with a clear log instead of
            # error-spamming every 15 minutes forever. An UNHEALTHY or
            # stopped green may still recover, so those stay STAGED.
            # NOTE: promote_container() raises RuntimeError (not
            # docker.errors.NotFound) for a missing green — match its
            # "not found" message too (covered by
            # test_missing_green_fails_row_fast).
            if isinstance(exc, docker.errors.NotFound) or (
                isinstance(exc, RuntimeError)
                and "not found" in str(exc).lower()
            ):
                try:
                    deployment.status = Deployment.Status.FAILED
                    deployment.finished_at = timezone.now()
                    deployment.save(update_fields=["status", "finished_at", "updated_at"])
                    append_log(
                        deployment,
                        "[AUTO-PROMOTE] Green container is gone — cannot promote. "
                        "Marked FAILED; redeploy to ship a fresh build.\n",
                    )
                    logger.warning(
                        "Auto-promote: green gone for deployment %s — marked FAILED",
                        deployment.id,
                    )
                except Exception as inner:
                    logger.exception("Auto-promote: failed to fail deployment %s: %s", deployment.id, inner)
                continue
            logger.exception("Auto-promote failed for deployment %s: %s", deployment.id, exc)

    return {'promoted': promoted, 'skipped': skipped}


@shared_task(
    name="apps.deployments.tasks.reap_unhealthy_staged_deployments",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def reap_unhealthy_staged_deployments():
    """Fail STAGED deployments whose green container is verifiably dead.

    A green that is missing, exited, unhealthy, or still warming far past
    any sane start period can never promote — leaving the row STAGED
    forever 503s the service on a dead backend while the UI suggests it
    is "awaiting review" (2026-09-14: policy-service green stuck
    health=starting for 9h with no blue container left at all).

    Safety: only rows older than STALE_STAGED_GREEN_REAP_HOURS are
    examined, and healthy (or healthcheck-less but running) greens are
    NEVER touched regardless of age. The container is force-removed so a
    crash-looping green stops consuming resources; the failure is logged
    to the deployment for redeploy guidance.
    """
    from apps.deployments.constants import (
        STALE_STAGED_GREEN_BATCH_SIZE,
        STALE_STAGED_GREEN_REAP_HOURS,
    )
    threshold = timezone.now() - timedelta(hours=STALE_STAGED_GREEN_REAP_HOURS)
    staged = Deployment.objects.filter(
        status=Deployment.Status.STAGED,
        staged_at__lte=threshold,
    ).select_related('service')[:STALE_STAGED_GREEN_BATCH_SIZE]

    reaped = 0
    for deployment in staged:
        # Canary-active HEALTHY greens are intentionally long-lived (they
        # serve weighted production traffic) — exempt below. But a
        # canary green that is verifiably dead (missing/exited/unhealthy)
        # must still be reaped: otherwise a split to a dead backend 502s
        # a share of traffic with no recovery path.
        canary_active = False
        try:
            from apps.deployments.services.safedeploy.promotion_guard import (
                is_canary_active,
            )
            canary_active = bool(is_canary_active(getattr(deployment, 'service', None)))
        except Exception as exc:
            logger.debug("Green reaper canary check failed: %s", exc)
        green_id = (deployment.green_container_id or "").strip()
        if not green_id:
            _fail_staged_green(
                deployment,
                "No green container ID on a STAGED row — it can never "
                "promote. Marked FAILED; redeploy to ship a fresh build.",
                remove_container=False,
            )
            reaped += 1
            continue
        try:
            green = docker.from_env().containers.get(green_id)
            green.reload()
        except docker.errors.NotFound:
            _fail_staged_green(
                deployment,
                "Green container is gone (GC'd or crashed away) — it can "
                "never promote. Marked FAILED; redeploy to ship a fresh build.",
                remove_container=False,
            )
            reaped += 1
            continue
        except Exception as exc:
            logger.warning(
                "Green reaper: cannot inspect green %s for deployment %s: %s",
                green_id[:12], deployment.id, exc,
            )
            continue
        try:
            state = green.attrs.get('State', {}) or {}
            status = (state.get('Status') or '').lower()
            health = (state.get('Health', {}).get('Status') or '').lower()
        except Exception:
            status, health = '', ''
        if status == 'running' and health in ('healthy', ''):
            # Legitimately held for review (or no healthcheck configured).
            # Canary-active healthy greens are additionally long-lived by
            # design — the split governs them, not the reaper.
            if canary_active:
                logger.info(
                    "Green reaper: skipping healthy canary-active deployment %s",
                    getattr(deployment, 'id', '?'),
                )
            continue
        reason = (
            f"Green container is {status or 'unknown'}"
            f"{f' (health={health})' if health else ''} after "
            f"{STALE_STAGED_GREEN_REAP_HOURS}h+ staged — it can never "
            f"promote. Marked FAILED and removed; redeploy to ship a "
            f"fresh build."
        )
        _fail_staged_green(deployment, reason, remove_container=True)
        reaped += 1

    return {'reaped': reaped}


def _fail_staged_green(
    deployment: Deployment, reason: str, remove_container: bool,
) -> None:
    """Mark a stuck STAGED row FAILED, drop its dead green, and log it."""
    green_id = (deployment.green_container_id or "").strip()
    # A reaped green must never keep receiving canary traffic.
    try:
        from apps.deployments.services.traefik_manager.canary_file import (
            remove_canary_file,
        )
        remove_canary_file(getattr(deployment, "service", None))
    except Exception as exc:
        logger.debug("Canary file cleanup on green reap failed: %s", exc)
    green_id = (deployment.green_container_id or "").strip()
    if remove_container and green_id:
        try:
            dead = docker.from_env().containers.get(green_id)
            dead.remove(force=True)
            logger.info(
                "Green reaper: removed dead green %s for deployment %s",
                green_id[:12], deployment.id,
            )
        except docker.errors.NotFound:
            pass
        except Exception as exc:
            logger.warning(
                "Green reaper: could not remove green %s: %s",
                green_id[:12], exc,
            )
    try:
        deployment.status = Deployment.Status.FAILED
        deployment.finished_at = timezone.now()
        deployment.save(update_fields=["status", "finished_at", "updated_at"])
        append_log(deployment, f"[GREEN-REAPER] {reason}\n")
        broadcast_status(deployment)
    except Exception as exc:
        logger.exception(
            "Green reaper: failed to fail deployment %s: %s", deployment.id, exc
        )


@shared_task(
    name="apps.deployments.tasks.auto_review_deployments",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def auto_review_deployments():
    """Auto-approve deployments stuck in REVIEW status for longer than configured hours."""
    hours = _get_config_hours('auto_review_hours', 2)
    if hours <= 0:
        return {'approved': 0, 'skipped': 'disabled'}

    threshold = timezone.now() - timedelta(hours=hours)
    reviews = Deployment.objects.filter(
        status=Deployment.Status.REVIEW,
        created_at__lte=threshold,
    ).select_related('service')

    approved = 0
    for deployment in reviews:
        try:
            from ..views._helpers import _resolve_provider_for_target
            provider = _resolve_provider_for_target(
                deployment.service,
                target_is_local=bool(getattr(deployment, 'target_is_local', False)),
            )
            if not provider:
                logger.warning("Auto-review: no provider for %s, skipping", deployment.service.name)
                continue

            deployment.status = Deployment.Status.BUILDING
            deployment.started_at = timezone.now()
            deployment.save(update_fields=['status', 'started_at'])

            from .build import resume_deploy_task
            resume_deploy_task.delay(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
            )

            append_log(
                deployment,
                f"[AUTO-REVIEW] Deployment auto-approved after {hours} hours. Build starting.\n"
            )
            approved += 1
        except Exception as exc:
            logger.exception("Auto-review failed for deployment %s: %s", deployment.id, exc)

    return {'approved': approved}
