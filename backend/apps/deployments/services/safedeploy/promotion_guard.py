"""STAGED → ACTIVE promotion readiness gate.

Manual ``POST /deployments/{id}/promote/`` and the
``auto_promote_staged_deployments`` sweep both consult
:func:`check_promotion_readiness` BEFORE calling ``_do_promote``.

Policy resolution (every key configurable):
platform-wide defaults live on ``PlatformConfig.promote_*``;
``Service.promotion_policy`` (JSON dict, same keys without the prefix)
overrides individual keys per service. Missing keys inherit.

This module performs checks only — it never mutates. Check-level
failures degrade to warnings, never exceptions (a monitoring blip must
not wedge promotions); known-unsafe states are blockers.
"""

import logging
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Canonical policy keys (service-level names; platform fields add prefix).
POLICY_KEYS = (
    'require_green_healthy',
    'min_staging_seconds',
    'require_migration_passed',
    'require_approval_high_critical',
    'block_when_canary_active',
    'block_contract_unsafe',
    'canary_get_only',
    'canary_sticky',
)

DEFAULT_PROMOTION_POLICY: dict[str, Any] = {
    'require_green_healthy': True,
    'min_staging_seconds': 0,
    'require_migration_passed': False,
    'require_approval_high_critical': True,
    'block_when_canary_active': False,
    'block_contract_unsafe': False,
    'canary_get_only': False,
    'canary_sticky': False,
}

_PLATFORM_FIELD_PREFIX = 'promote_'


def get_promotion_policy(service=None) -> dict[str, Any]:
    """Merge platform defaults with per-service overrides."""
    policy = dict(DEFAULT_PROMOTION_POLICY)
    try:
        from apps.deployments.models import PlatformConfig

        config = PlatformConfig.load()
        for key in POLICY_KEYS:
            field = f'{_PLATFORM_FIELD_PREFIX}{key}'
            value = getattr(config, field, None)
            if value is not None:
                policy[key] = value
    except Exception as exc:
        logger.debug("Promotion policy platform lookup failed, using defaults: %s", exc)
    try:
        overrides = getattr(service, 'promotion_policy', None) or {}
        if isinstance(overrides, dict):
            for key in POLICY_KEYS:
                if key in overrides and overrides[key] is not None:
                    if key == 'min_staging_seconds':
                        policy[key] = max(0, int(overrides[key]))
                    else:
                        policy[key] = bool(overrides[key])
    except Exception as exc:
        logger.debug("Promotion policy service override ignored: %s", exc)
    return policy


def is_canary_active(service) -> bool:
    """Shared-DB canary split currently enabled on this service."""
    try:
        strategy = str(getattr(service, 'deploy_strategy', '') or '').upper()
        pct = int(getattr(service, 'canary_percentage', 0) or 0)
        return strategy == 'CANARY' and pct > 0
    except (TypeError, ValueError):
        return False


def _green_container_state(green_id: str) -> tuple[str, str]:
    """Inspect the green container (module seam for tests).

    Returns ``(status, health)`` lower-cased; ``health`` is ``''`` when
    no healthcheck is configured. Raises on inspect failure.
    """
    import docker

    green = docker.from_env().containers.get(green_id)
    green.reload()
    state = green.attrs.get('State', {}) or {}
    status = str(state.get('Status') or '').lower()
    health = str((state.get('Health', {}) or {}).get('Status') or '').lower()
    return status, health


def _validation_for_deployment(deployment):
    """Commit-scoped MigrationValidation for this deployment (seam)."""
    from apps.deployments.services.safedeploy.canary_guard import (
        _validation_for_commit,
    )

    service = getattr(deployment, 'service', None)
    commit = getattr(deployment, 'commit_hash', None)
    if not getattr(service, 'id', None) or not commit:
        return None
    try:
        return _validation_for_commit(service.id, commit)
    except Exception as exc:
        logger.debug("Promotion validation lookup failed: %s", exc)
        return None


def _approval_exists(deployment) -> bool:
    """APPROVED DeploymentApproval present for this deployment (seam)."""
    from apps.deployments.models.safedeploy import DeploymentApproval

    try:
        return DeploymentApproval.objects.filter(
            deployment=deployment,
            status=DeploymentApproval.Status.APPROVED,
        ).exists()
    except Exception as exc:
        logger.debug("Promotion approval lookup failed: %s", exc)
        return False


def _provider_is_local(provider) -> bool | None:
    """True = local docker target, False = remote/lite, None = unknown."""
    if provider is None:
        return None
    try:
        from apps.cloud.models import CloudProvider

        return provider.provider_type == CloudProvider.ProviderType.LOCAL
    except Exception:
        return None


def check_promotion_readiness(
    deployment, provider=None, now=None,
    require_staged_status=True, green_container_id=None,
) -> dict[str, Any]:
    """Evaluate whether a STAGED deployment may promote.

    ``require_staged_status`` is False only for the adapter hold
    fast-path, which gates the green BEFORE the row reaches STAGED.
    ``green_container_id`` overrides the row value for the same reason
    (the adapter holds the fresh green id in hand; the row isn't
    updated yet). Returns ``{'ready': bool, 'blockers': [...],
    'warnings': [...], 'policy': {...}}``. Never raises.
    """
    from django.utils import timezone

    now = now or timezone.now()
    service = getattr(deployment, 'service', None)
    policy = get_promotion_policy(service)
    blockers: list[str] = []
    warnings: list[str] = []

    # 1. Must be STAGED (callers ensure this; double-check defensively).
    if require_staged_status:
        try:
            from apps.deployments.models.core import Deployment

            if str(getattr(deployment, 'status', '') or '') != str(Deployment.Status.STAGED):
                blockers.append(
                    f"Deployment is {getattr(deployment, 'status', '?')}, not STAGED."
                )
                return {'ready': False, 'blockers': blockers, 'warnings': warnings, 'policy': policy}
        except Exception as exc:
            logger.debug("Promotion status check failed: %s", exc)

    # 2. Green container must exist.
    green_id = str(green_container_id or getattr(deployment, 'green_container_id', '') or '').strip()
    if not green_id:
        blockers.append("No green container ID on deployment — cannot promote.")
        return {'ready': False, 'blockers': blockers, 'warnings': warnings, 'policy': policy}

    # 3. Green health (local targets only; remote greens can't be
    # inspected from the controller).
    locality = _provider_is_local(provider)
    if locality is False:
        warnings.append(
            "Green health cannot be verified on remote/lite targets — "
            "promotion proceeds on the target's own health gates."
        )
    else:
        try:
            status, health = _green_container_state(green_id)
            healthy = status == 'running' and health in ('healthy', '')
            if not healthy:
                msg = (
                    f"Green container is {status or 'unknown'}"
                    f"{f' (health={health})' if health else ''} — "
                    "not safe to promote."
                )
                if policy['require_green_healthy']:
                    blockers.append(msg)
                else:
                    warnings.append(msg + " (proceeding: require_green_healthy is off)")
        except Exception as exc:
            warnings.append(f"Could not verify green container health: {exc}")

    # 4. Soak time.
    try:
        min_seconds = int(policy['min_staging_seconds'] or 0)
    except (TypeError, ValueError):
        min_seconds = 0
    if min_seconds > 0:
        staged_at = getattr(deployment, 'staged_at', None)
        if staged_at is None:
            blockers.append("staged_at is unknown — soak time cannot be verified.")
        else:
            try:
                waited = (now - staged_at).total_seconds()
            except Exception:
                waited = -1
            if waited < 0:
                blockers.append("staged_at is unknown — soak time cannot be verified.")
            elif waited < min_seconds:
                remaining = int(min_seconds - waited)
                blockers.append(
                    f"Soak time not met: staged {int(waited)}s ago, "
                    f"promotion requires {min_seconds}s ({remaining}s remaining)."
                )

    # 5 + 6. Migration validation + approval (commit-scoped).
    validation = _validation_for_deployment(deployment)
    risk_level = None
    if validation is not None:
        try:
            from apps.deployments.models.safedeploy import MigrationValidation

            vstatus = str(getattr(validation, 'status', '') or '')
            risk_level = str(getattr(validation, 'risk_level', '') or '')
            unconfigured = vstatus in (
                str(MigrationValidation.Status.NOT_CONFIGURED),
                str(MigrationValidation.Status.SKIPPED),
            )
            if not unconfigured:
                if policy['require_migration_passed']:
                    if vstatus != str(MigrationValidation.Status.PASSED):
                        blockers.append(
                            f"Migration validation is {vstatus or 'unknown'} — "
                            "require_migration_passed blocks promotion."
                        )
                elif vstatus in (
                    str(MigrationValidation.Status.FAILED),
                    str(MigrationValidation.Status.INCOMPLETE),
                ):
                    warnings.append(
                        f"Migration validation is {vstatus} "
                        "(proceeding: require_migration_passed is off)."
                    )
                if (
                    policy['require_approval_high_critical']
                    and risk_level in ('HIGH', 'CRITICAL')
                ):
                    if not _approval_exists(deployment):
                        blockers.append(
                            f"Migration risk is {risk_level} without an APPROVED "
                            "DeploymentApproval — promotion blocked."
                        )
        except Exception as exc:
            logger.debug("Promotion validation evaluation failed: %s", exc)
    elif policy['require_migration_passed']:
        blockers.append(
            "No MigrationValidation for this commit — "
            "require_migration_passed blocks promotion."
        )

    # 7. Active canary split + contract safety.
    try:
        if is_canary_active(service):
            if policy['block_when_canary_active']:
                blockers.append(
                    "A canary split is active — ramp to 100% or set "
                    "canary_percentage to 0 before promoting."
                )
            else:
                from apps.deployments.services.safedeploy.canary_guard import (
                    validate_canary_enable,
                )

                allowed, reasons = validate_canary_enable(
                    service,
                    validation=validation,
                    commit_hash=getattr(deployment, 'commit_hash', None),
                )
                if not allowed:
                    msg = (
                        "Contract-unsafe migrations under an active canary: "
                        + (reasons[0] if reasons else "not expand-safe.")
                        + " Post-promote rollback will be impossible."
                    )
                    if policy['block_contract_unsafe']:
                        blockers.append(msg)
                    else:
                        warnings.append(msg)
    except Exception as exc:
        logger.debug("Promotion canary evaluation failed: %s", exc)

    return {
        'ready': not blockers,
        'blockers': blockers,
        'warnings': warnings,
        'policy': policy,
    }


def format_readiness(result: dict[str, Any]) -> str:
    """One-line human summary for logs / API messages."""
    if result.get('ready'):
        extra = f" ({len(result.get('warnings', []))} warnings)" if result.get('warnings') else ""
        return f"Promotion ready{extra}."
    return "Promotion blocked: " + "; ".join(result.get('blockers', []) or ['unknown reason'])
