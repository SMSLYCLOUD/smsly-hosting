"""Shared-DB canary gate (expand/contract enforcement).

A weighted traffic split runs old + new code concurrently against the SAME
database. Any contract op (RemoveField/DeleteModel/Rename/Alter/RunSQL,
NOT NULL AddField without default) breaks the old version mid-split.

This module is the single rule both the API serializer and any future
Caddy-weight writer consult before allowing ``canary_percentage > 0``:

    from apps.deployments.services.safedeploy.canary_guard import (
        canary_allowed_for_report,
        validate_canary_enable,
    )

No schema change required: it reads the existing ``MigrationValidation``
row (risk_level + detected_operations/reasons JSON).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Contract-unsafe operation types. Must stay in sync with
# DjangoAdapter.classify_migration_risk in django_adapter.py.
_CONTRACT_OP_TYPES = frozenset({
    'DeleteModel', 'RemoveField', 'RenameField', 'RenameModel',
    'AlterField', 'RunSQL', 'RunPython', 'RemoveIndex',
})

_BLOCKED_RISK_LEVELS = frozenset({'HIGH', 'CRITICAL'})


def canary_allowed_for_report(report: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Evaluate a classify_migration_risk() report for canary safety.

    Returns ``(allowed, block_reasons)``. ``None`` (no migration data, e.g.
    non-Django service or NOT_CONFIGURED) → allowed: there is nothing
    known-unsafe to block on.
    """
    if not report:
        return True, []
    # Prefer the adapter's own verdict when present (it sees AddField
    # nullability); fall back to risk-level + op-type heuristics otherwise.
    if 'canary_allowed' in report:
        reasons = list(report.get('canary_block_reasons') or [])
        return bool(report.get('canary_allowed')), reasons
    reasons: list[str] = []
    if str(report.get('risk_level') or '') in _BLOCKED_RISK_LEVELS:
        reasons.append(
            f"Migration risk is {report.get('risk_level')} — destructive ops "
            "break the old version while both share the DB. Use "
            "expand/contract: ship additive changes first, contract later."
        )
    for op in report.get('detected_operations') or []:
        op_type = str((op or {}).get('type') or '')
        if op_type in _CONTRACT_OP_TYPES:
            reasons.append(
                f"Contract op blocks shared-DB canary: {op_type} — ship it "
                "in a later release after the canary is at 100%."
            )
    return (not reasons), reasons


def _validation_for_commit(service_id, commit_hash):
    """Commit-scoped MigrationValidation lookup (module seam for tests)."""
    from apps.deployments.models.safedeploy import MigrationValidation

    return (
        MigrationValidation.objects.filter(
            deployment__service_id=service_id,
            deployment__commit_hash=commit_hash,
        )
        .order_by('-created_at')
        .first()
    )


def _staged_commit_for_service(service):
    """Commit hash of the service's active STAGED deployment, if any."""
    try:
        deployments = getattr(service, 'deployments', None)
        if deployments is None:
            return None
        from apps.deployments.models.core import Deployment

        staged = (
            deployments.filter(status=Deployment.Status.STAGED)
            .order_by('-staged_at', '-created_at')
            .first()
        )
        commit = getattr(staged, 'commit_hash', None)
        return str(commit).strip() if commit else None
    except Exception:
        return None


def validate_canary_enable(service, validation=None, commit_hash=None) -> tuple[bool, list[str]]:
    """Can ``service`` run a shared-DB canary right now?

    ``validation`` may be a ``MigrationValidation`` instance, a report dict,
    or None (looked up — commit-scoped when ``commit_hash`` is given or an
    active STAGED deployment exists, else latest for the service). Returns
    ``(allowed, block_reasons)``. Isolated-DB previews (DatabaseClone) never
    need this gate — only the shared-DB weighted split does.

    Total function: unexpected lookup failures fail OPEN with a warning log
    (a DB blip must not wedge deploys); known-unsafe data fails CLOSED.
    """
    try:
        return _validate_canary_enable_inner(service, validation, commit_hash)
    except Exception as exc:
        logger.warning("Canary guard lookup failed, allowing: %s", exc)
        return True, []


def _validate_canary_enable_inner(service, validation, commit_hash) -> tuple[bool, list[str]]:
    report: dict[str, Any] | None = None
    if validation is None:
        from apps.deployments.models.safedeploy import MigrationValidation

        service_id = getattr(service, 'id', None)
        if commit_hash is None:
            commit_hash = _staged_commit_for_service(service)
        if commit_hash:
            try:
                validation = _validation_for_commit(service_id, commit_hash)
            except Exception as exc:
                logger.debug("Commit-scoped validation lookup failed: %s", exc)
                validation = None
        if validation is None:
            try:
                validation = (
                    MigrationValidation.objects.filter(
                        deployment__service_id=service_id,
                    )
                    .order_by('-created_at')
                    .first()
                )
            except Exception as exc:
                logger.debug("Latest validation lookup failed: %s", exc)
                validation = None
    if validation is None:
        return True, []
    if isinstance(validation, dict):
        report = validation
    else:
        status = str(getattr(validation, 'status', '') or '')
        if status in ('NOT_CONFIGURED', 'SKIPPED'):
            return True, []
        detected = getattr(validation, 'detected_operations', None)
        ops = list(detected) if isinstance(detected, (list, tuple)) else []
        if ops:
            # Recompute from stored ops: the adapter sees AddField
            # nullability, which the fallback heuristic below cannot.
            # (Rows written before flags existed recompute conservatively —
            # re-run validation to clear a stale block.)
            try:
                from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter

                return canary_allowed_for_report(
                    DjangoAdapter().classify_migration_risk(ops)
                )
            except Exception as exc:
                logger.debug("Canary verdict recompute failed: %s", exc)
        report = {
            'risk_level': str(getattr(validation, 'risk_level', '') or ''),
            'detected_operations': ops,
            'canary_block_reasons': [],
        }
        # Surface stored expand/contract reasons if a previous run saved
        # them into reasons/recommendations JSON.
        for extra in list(getattr(validation, 'reasons', None) or []) + list(
            getattr(validation, 'recommendations', None) or []
        ):
            if isinstance(extra, str) and 'canary' in extra.lower():
                report['canary_block_reasons'].append(extra)
        if report['canary_block_reasons']:
            report['canary_allowed'] = False
    return canary_allowed_for_report(report)
