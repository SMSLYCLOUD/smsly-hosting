"""Celery background tasks for SMSLY Aegis continuous security audits, OCSF ingest, and DAST."""
from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.security.tasks.sync_security_telemetry_task",
    soft_time_limit=180,
    time_limit=210,
)
def sync_security_telemetry_task():
    """Normalize AuditLog security events + match Threat Intel IoCs into the OCSF lake."""
    from .ocsf import ingest_from_audit_and_alerts
    from .xdr import match_threat_intel

    try:
        ingested = ingest_from_audit_and_alerts(hours=24)
        ti = match_threat_intel()
        return {"ingested": ingested, "threat_intel_matches": len(ti.get("matches", []))}
    except SoftTimeLimitExceeded:
        logger.warning("sync_security_telemetry_task hit soft time limit")
        return {"status": "timed_out"}


def prune_security_telemetry(retention_days: int = 90) -> dict[str, int]:
    """Prune expired NormalizedSecurityEvent and ForensicSnapshot rows to bound DB growth."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import ForensicSnapshot, NormalizedSecurityEvent

    cutoff = timezone.now() - timedelta(days=max(7, int(retention_days or 90)))
    deleted_events, _ = NormalizedSecurityEvent.objects.filter(occurred_at__lt=cutoff).delete()
    deleted_snaps, _ = ForensicSnapshot.objects.filter(created_at__lt=cutoff).delete()
    return {"deleted_events": deleted_events, "deleted_snapshots": deleted_snaps}


@shared_task(
    name="apps.security.tasks.run_cnapp_xdr_audit_task",
    soft_time_limit=240,
    time_limit=300,
)
def run_cnapp_xdr_audit_task():
    """Execute full CNAPP (CSPM, KSPM, CIEM, DSPM, AI-SPM), XDR ITDR, and GRC audits."""
    from .cnapp import (
        run_ai_spm_audit,
        run_ciem_audit,
        run_cspm_audit,
        run_dspm_audit,
        run_kspm_audit,
    )
    from .compliance import evaluate_all_compliance_controls
    from .xdr import run_itdr_ueba_scan

    try:
        return {
            "cspm": run_cspm_audit(),
            "kspm": run_kspm_audit(),
            "ciem": run_ciem_audit(),
            "dspm": run_dspm_audit(),
            "ai_spm": run_ai_spm_audit(),
            "itdr": run_itdr_ueba_scan(hours=24),
            "compliance": evaluate_all_compliance_controls()["frameworks"],
            "retention": prune_security_telemetry(retention_days=90),
        }
    except SoftTimeLimitExceeded:
        logger.warning("run_cnapp_xdr_audit_task hit soft time limit")
        return {"status": "timed_out"}


@shared_task(
    name="apps.security.tasks.run_post_deploy_dast_task",
    soft_time_limit=120,
    time_limit=150,
)
def run_post_deploy_dast_task(service_id: str, deployment_id: str | None = None):
    """Run SSRF-safe post-deploy DAST checks on a newly promoted service."""
    from apps.deployments.models import Deployment, Service
    from .models import SecurityPolicy
    from .xdr import run_post_deploy_dast

    try:
        policy = SecurityPolicy.load()
        if not policy.dast_on_deploy_enabled:
            return {"skipped": True, "reason": "dast_on_deploy_enabled=False"}
        svc = Service.objects.filter(id=service_id).first()
        if svc is None:
            return {"skipped": True, "reason": "service_not_found"}
        dep = Deployment.objects.filter(id=deployment_id).first() if deployment_id else None
        return run_post_deploy_dast(svc, deployment=dep)
    except SoftTimeLimitExceeded:
        logger.warning("run_post_deploy_dast_task timed out for service %s", service_id)
        return {"status": "timed_out"}
