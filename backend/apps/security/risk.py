"""Risk Fusion Scoring & Deduplicated Finding Lifecycle Engine."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

from .models import SecurityFinding, SecurityPolicy
from .ocsf import infer_mitre_mapping, record_ocsf_event

logger = logging.getLogger(__name__)

_SEVERITY_BASE_SCORE = {
    "CRITICAL": 65,
    "HIGH": 48,
    "MEDIUM": 28,
    "LOW": 14,
    "INFO": 5,
}

_SENSITIVE_ADDON_TYPES = frozenset({
    "POSTGRES", "MYSQL", "MARIADB", "COCKROACHDB", "TIMESCALEDB", "PERCONA",
    "MONGODB", "COUCHDB", "REDIS", "VALKEY", "KEYDB", "DRAGONFLYDB",
    "MINIO", "SEAWEEDFS", "VAULT", "KEYCLOAK",
})


def inspect_service_exposure(service) -> tuple[bool, bool, bool]:
    """Return (internet_exposed, data_sensitive, privileged_weakness) for a Service."""
    if service is None:
        return False, False, False

    internet_exposed = bool(
        getattr(service, "is_public", True)
        and (
            getattr(service, "public_domain", "")
            or getattr(service, "custom_domains", None)
        )
    )

    data_sensitive = False
    try:
        if service.addons.filter(addon_type__in=_SENSITIVE_ADDON_TYPES).exists():
            data_sensitive = True
        elif service.env_vars.filter(is_secret=True).exists():
            data_sensitive = True
    except Exception:
        pass

    privileged_weakness = bool(
        getattr(service, "waf_opt_out", False)
        or (data_sensitive and not getattr(service, "encryption_at_rest_enabled", False))
    )
    return internet_exposed, data_sensitive, privileged_weakness


def compute_risk_fusion(
    *,
    severity: str,
    service=None,
    epss_score: float = 0.0,
    internet_exposed: bool | None = None,
    data_sensitive: bool | None = None,
    privileged: bool | None = None,
) -> tuple[int, dict[str, bool]]:
    """Compute contextual Risk Fusion score (1..100) matching Aeon Core prioritization."""
    sev = str(severity or "MEDIUM").upper()
    base = _SEVERITY_BASE_SCORE.get(sev, 28)

    svc_exp, svc_sens, svc_priv = inspect_service_exposure(service)
    exp = svc_exp if internet_exposed is None else bool(internet_exposed)
    sens = svc_sens if data_sensitive is None else bool(data_sensitive)
    priv = svc_priv if privileged is None else bool(privileged)

    score = base
    if exp:
        score += 14
    if sens:
        score += 12
    if priv:
        score += 8
    clamped_epss = max(0.0, min(float(epss_score or 0.0), 1.0))
    score += int(round(clamped_epss * 12))
    return max(1, min(100, score)), {
        "internet_exposed": exp,
        "data_sensitive": sens,
        "privileged": priv,
    }


def _compute_sla_deadline(severity: str, policy: SecurityPolicy) -> timezone.datetime | None:
    now = timezone.now()
    sev = str(severity or "MEDIUM").upper()
    if sev == "CRITICAL":
        return now + timedelta(hours=max(1, policy.sla_critical_hours))
    if sev == "HIGH":
        return now + timedelta(days=max(1, policy.sla_high_days))
    if sev == "MEDIUM":
        return now + timedelta(days=max(1, policy.sla_medium_days))
    return None


def upsert_finding(
    *,
    fingerprint: str,
    pillar: str,
    category: str,
    severity: str,
    title: str,
    description: str = "",
    remediation: str = "",
    service=None,
    deployment=None,
    server=None,
    package_name: str = "",
    installed_version: str = "",
    fixed_version: str = "",
    file_path: str = "",
    line_number: int | None = None,
    cve_id: str = "",
    cwe_id: str = "",
    epss_score: float = 0.0,
    internet_exposed: bool | None = None,
    data_sensitive: bool | None = None,
    privileged: bool | None = None,
    evidence: dict[str, Any] | None = None,
    emit_ocsf: bool = False,
) -> SecurityFinding:
    """Create or refresh a SecurityFinding while respecting analyst suppressions."""
    norm_sev = str(severity or "MEDIUM").upper()
    if norm_sev not in SecurityFinding.Severity.values:
        norm_sev = SecurityFinding.Severity.MEDIUM

    risk_score, flags = compute_risk_fusion(
        severity=norm_sev,
        service=service,
        epss_score=epss_score,
        internet_exposed=internet_exposed,
        data_sensitive=data_sensitive,
        privileged=privileged,
    )
    tactic, tech_id, tech_name = infer_mitre_mapping(
        source=category.lower(),
        category=category,
        title=title,
        details=description,
    )

    try:
        policy = SecurityPolicy.load()
        sla_due = _compute_sla_deadline(norm_sev, policy)
    except Exception:
        sla_due = None

    existing = SecurityFinding.objects.filter(fingerprint=fingerprint).first()
    newly_opened_critical = False
    if existing is not None:
        # Reopen previously RESOLVED findings if detected again; keep FALSE_POSITIVE/RISK_ACCEPTED unless expired.
        reopened = False
        if existing.status == SecurityFinding.Status.RESOLVED:
            existing.status = SecurityFinding.Status.OPEN
            existing.resolved_at = None
            reopened = True
        elif existing.status in (SecurityFinding.Status.FALSE_POSITIVE, SecurityFinding.Status.RISK_ACCEPTED):
            if existing.suppressed_until and existing.suppressed_until <= timezone.now():
                existing.status = SecurityFinding.Status.OPEN
                existing.suppressed_until = None
                reopened = True
        if reopened and norm_sev == SecurityFinding.Severity.CRITICAL:
            newly_opened_critical = True
        existing.severity = norm_sev
        existing.risk_score = risk_score
        existing.epss_score = max(existing.epss_score, float(epss_score or 0.0))
        existing.internet_exposed = flags["internet_exposed"]
        existing.data_sensitive = flags["data_sensitive"]
        existing.privileged = flags["privileged"]
        existing.title = str(title)[:255]
        if description:
            existing.description = description
        if remediation:
            existing.remediation = remediation
        if deployment is not None:
            existing.deployment = deployment
        if server is not None:
            existing.server = server
        if fixed_version:
            existing.fixed_version = fixed_version[:64]
        if evidence:
            existing.evidence = evidence
        if sla_due and (not existing.sla_due_at or reopened):
            existing.sla_due_at = sla_due
        existing.save()
        finding = existing
    else:
        newly_opened_critical = norm_sev == SecurityFinding.Severity.CRITICAL
        finding = SecurityFinding.objects.create(
            fingerprint=fingerprint,
            pillar=pillar,
            category=category,
            severity=norm_sev,
            status=SecurityFinding.Status.OPEN,
            risk_score=risk_score,
            epss_score=float(epss_score or 0.0),
            internet_exposed=flags["internet_exposed"],
            data_sensitive=flags["data_sensitive"],
            privileged=flags["privileged"],
            title=str(title)[:255],
            description=description,
            remediation=remediation,
            package_name=str(package_name or "")[:150],
            installed_version=str(installed_version or "")[:64],
            fixed_version=str(fixed_version or "")[:64],
            file_path=str(file_path or "")[:255],
            line_number=line_number,
            cve_id=str(cve_id or "")[:64],
            cwe_id=str(cwe_id or "")[:32],
            mitre_tactic=tactic,
            mitre_technique=tech_id,
            mitre_technique_name=tech_name,
            service=service,
            deployment=deployment,
            server=server,
            evidence=evidence or {},
            sla_due_at=sla_due,
        )

    if emit_ocsf and finding.status == SecurityFinding.Status.OPEN:
        try:
            target = (
                getattr(service, "name", "")
                or getattr(server, "name", "")
                or finding.file_path
                or finding.package_name
                or "platform"
            )
            record_ocsf_event(
                source=category.lower(),
                source_uid=f"finding-{finding.fingerprint[:24]}",
                title=finding.title,
                details=finding.description[:400],
                severity=finding.severity,
                target=target,
                category=category,
                service=service,
                deployment=deployment,
                server=server,
                raw={"finding_id": str(finding.id), "risk_score": finding.risk_score},
            )
        except Exception as exc:
            logger.debug("OCSF event emit skipped for finding %s: %s", finding.id, exc)

    if newly_opened_critical and finding.status == SecurityFinding.Status.OPEN:
        try:
            from django.core.cache import cache
            from apps.notifications.tasks import dispatch_notification

            cooldown_key = f"smsly:sec:crit-notify:{finding.fingerprint[:32]}"
            if cache.add(cooldown_key, 1, timeout=3600):
                owner_id = getattr(service, "owner_id", None) if service is not None else None
                if owner_id:
                    dispatch_notification.delay(
                        event_type="security_critical",
                        user_id=owner_id,
                        title=f"CRITICAL Security Finding: {finding.title[:120]}",
                        message=(
                            f"[{finding.pillar}/{finding.category}] Risk Score {finding.risk_score}/100\n"
                            f"{finding.description[:300]}\nRemediation: {finding.remediation[:200]}"
                        ),
                    )
        except Exception as exc:
            logger.debug("Critical finding notification dispatch skipped: %s", exc)

    return finding


def resolve_stale_findings(
    *,
    category: str,
    active_fingerprints: set[str],
    service=None,
) -> int:
    """Resolve OPEN/ACKNOWLEDGED findings in a category that disappeared on re-scan."""
    qs = SecurityFinding.objects.filter(
        category=category,
        status__in=[SecurityFinding.Status.OPEN, SecurityFinding.Status.ACKNOWLEDGED],
    )
    if service is not None:
        qs = qs.filter(service=service)
    if active_fingerprints:
        qs = qs.exclude(fingerprint__in=active_fingerprints)
    return qs.update(status=SecurityFinding.Status.RESOLVED, resolved_at=timezone.now())
