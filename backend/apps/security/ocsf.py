"""OCSF v1.1 Telemetry Normalization & MITRE ATT&CK Mapping Engine."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.db import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import NormalizedSecurityEvent, SecurityFinding

logger = logging.getLogger(__name__)

# OCSF severity mapping:
# Unknown(0), Informational(1), Low(2), Medium(3), High(4), Critical(5), Fatal(6)
_SEVERITY_TO_OCSF_ID = {
    "INFO": 1,
    "LOW": 2,
    "MEDIUM": 3,
    "HIGH": 4,
    "WARNING": 3,
    "CRITICAL": 5,
    "FATAL": 6,
}

# MITRE ATT&CK matrix catalogue covered by SMSLY detection & defense engines.
MITRE_TECHNIQUE_CATALOG: list[dict[str, str]] = [
    {"tactic": "TA0001 Initial Access", "technique_id": "T1190", "name": "Exploit Public-Facing Application"},
    {"tactic": "TA0001 Initial Access", "technique_id": "T1133", "name": "External Remote Services"},
    {"tactic": "TA0002 Execution", "technique_id": "T1059", "name": "Command and Scripting Interpreter"},
    {"tactic": "TA0002 Execution", "technique_id": "T1609", "name": "Container Administration Command"},
    {"tactic": "TA0003 Persistence", "technique_id": "T1053", "name": "Scheduled Task/Job"},
    {"tactic": "TA0004 Privilege Escalation", "technique_id": "T1611", "name": "Escape to Host"},
    {"tactic": "TA0006 Credential Access", "technique_id": "T1110", "name": "Brute Force"},
    {"tactic": "TA0006 Credential Access", "technique_id": "T1552", "name": "Unsecured Credentials"},
    {"tactic": "TA0007 Discovery", "technique_id": "T1595", "name": "Active Scanning"},
    {"tactic": "TA0007 Discovery", "technique_id": "T1526", "name": "Cloud Service Discovery"},
    {"tactic": "TA0009 Collection", "technique_id": "T1530", "name": "Data from Cloud Storage"},
    {"tactic": "TA0011 Command and Control", "technique_id": "T1071", "name": "Application Layer Protocol"},
    {"tactic": "TA0040 Impact", "technique_id": "T1498", "name": "Network Denial of Service"},
    {"tactic": "TA0040 Impact", "technique_id": "T1485", "name": "Data Destruction"},
]


def _normalize_severity(raw_sev: str | None) -> tuple[str, int]:
    s = str(raw_sev or "INFO").strip().upper()
    if s == "WARNING":
        s = "MEDIUM"
    if s not in ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"):
        s = "INFO"
    return s, _SEVERITY_TO_OCSF_ID.get(s, 1)


def infer_mitre_mapping(
    *,
    source: str = "",
    category: str = "",
    title: str = "",
    details: str = "",
) -> tuple[str, str, str]:
    """Deterministically infer (tactic, technique_id, technique_name) from signal context."""
    text = f"{source} {category} {title} {details}".lower()

    if category == "SECRETS" or any(k in text for k in ("secret", "api_key", "private key", "token", "password", "credential")):
        return "TA0006 Credential Access", "T1552", "Unsecured Credentials"
    if any(k in text for k in ("bruteforce", "brute-force", "sshd", "caddy-auth", "login fail", "ban")):
        if "dos" in text or "flood" in text or "ddos" in text:
            return "TA0040 Impact", "T1498", "Network Denial of Service"
        return "TA0006 Credential Access", "T1110", "Brute Force"
    if any(k in text for k in ("shell spawned", "terminal shell", "bash", "exec", "command injection", "eval(", "subprocess")):
        return "TA0002 Execution", "T1059", "Command and Scripting Interpreter"
    if any(k in text for k in ("docker.sock", "privileged", "escape", "ptrace", "kptr", "cap_sys_admin", "privilege escalation")):
        return "TA0004 Privilege Escalation", "T1611", "Escape to Host"
    if any(k in text for k in ("sqli", "xss", "traversal", "cve", "waf", "openappsec", "coraza", "exploit", "dast", "sast")):
        return "TA0001 Initial Access", "T1190", "Exploit Public-Facing Application"
    if any(k in text for k in ("scan", "404", "probe", "crawler", "botnet", "subfinder")):
        return "TA0007 Discovery", "T1595", "Active Scanning"
    if category == "DSPM" or any(k in text for k in ("bucket", "database", "pii", "unencrypted", "encryption", "lockdown")):
        return "TA0009 Collection", "T1530", "Data from Cloud Storage"
    if category == "EDGE" or any(k in text for k in ("bgp", "rpki", "dns", "hijack", "tls", "ssl")):
        return "TA0011 Command and Control", "T1071", "Application Layer Protocol"
    if category in ("CSPM", "KSPM", "IAC", "CIEM"):
        return "TA0007 Discovery", "T1526", "Cloud Service Discovery"
    return "TA0001 Initial Access", "T1190", "Exploit Public-Facing Application"


def build_ocsf_payload(
    *,
    source: str,
    title: str,
    details: str = "",
    severity: str = "INFO",
    target: str = "",
    actor_ip: str = "",
    actor_user: str = "",
    mitre_tactic: str = "",
    mitre_technique: str = "",
    mitre_technique_name: str = "",
    occurred_at: datetime | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct an OCSF v1.1 compliant Detection/Security Finding JSON object."""
    norm_sev, sev_id = _normalize_severity(severity)
    ts = occurred_at or timezone.now()
    if not mitre_technique:
        mitre_tactic, mitre_technique, mitre_technique_name = infer_mitre_mapping(
            source=source, title=title, details=details
        )
    return {
        "metadata": {
            "version": "1.1.0",
            "product": {
                "name": "SMSLY Aegis Security Engine",
                "vendor_name": "SMSLY Hosting",
                "feature": {"name": source},
            },
        },
        "category_uid": 2,
        "category_name": "Findings",
        "class_uid": 2004,
        "class_name": "Detection Finding",
        "activity_id": 1,
        "activity_name": "Create",
        "severity_id": sev_id,
        "severity": norm_sev,
        "time": ts.isoformat(),
        "finding_info": {
            "title": title,
            "desc": details,
            "uid": hashlib.sha256(f"{source}|{title}|{target}|{ts.isoformat()}".encode()).hexdigest()[:24],
            "analytic": {"name": source, "type": "Rule"},
        },
        "attacks": [
            {
                "tactic": {"name": mitre_tactic},
                "technique": {"uid": mitre_technique, "name": mitre_technique_name},
            }
        ],
        "resources": [{"name": target, "type": "PlatformAsset"}] if target else [],
        "actor": {"user": {"name": actor_user} if actor_user else None, "ip": actor_ip or None},
        "unmapped": raw or {},
    }


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
    if isinstance(value, str) and value.strip():
        parsed = parse_datetime(value.strip().replace("Z", "+00:00"))
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_timezone.utc)
    return timezone.now()


def record_ocsf_event(
    *,
    source: str,
    source_uid: str,
    title: str,
    details: str = "",
    severity: str = "INFO",
    target: str = "",
    actor_ip: str = "",
    actor_user: str = "",
    category: str = "",
    service=None,
    deployment=None,
    server=None,
    occurred_at: datetime | None = None,
    raw: dict[str, Any] | None = None,
) -> NormalizedSecurityEvent:
    """Persist an OCSF v1.1 NormalizedSecurityEvent idempotently by (source, source_uid)."""
    norm_sev, sev_id = _normalize_severity(severity)
    ts = _parse_timestamp(occurred_at)
    tactic, tech_id, tech_name = infer_mitre_mapping(
        source=source, category=category, title=title, details=details
    )
    ocsf = build_ocsf_payload(
        source=source,
        title=title,
        details=details,
        severity=norm_sev,
        target=target,
        actor_ip=actor_ip,
        actor_user=actor_user,
        mitre_tactic=tactic,
        mitre_technique=tech_id,
        mitre_technique_name=tech_name,
        occurred_at=ts,
        raw=raw,
    )
    defaults = {
        "category_uid": 2,
        "category_name": "Findings",
        "class_uid": 2004,
        "class_name": "Detection Finding",
        "activity_name": "Detect",
        "severity_id": sev_id,
        "severity": norm_sev,
        "title": str(title)[:255],
        "details": str(details or ""),
        "target": str(target or "")[:255],
        "actor_ip": str(actor_ip or "")[:64],
        "actor_user": str(actor_user or "")[:150],
        "mitre_tactic": tactic[:64],
        "mitre_technique": tech_id[:32],
        "mitre_technique_name": tech_name[:128],
        "service": service,
        "deployment": deployment,
        "server": server,
        "raw_ocsf": ocsf,
        "occurred_at": ts,
    }
    try:
        obj, _created = NormalizedSecurityEvent.objects.update_or_create(
            source=str(source)[:32],
            source_uid=str(source_uid)[:128],
            defaults=defaults,
        )
    except IntegrityError:
        obj = NormalizedSecurityEvent.objects.get(
            source=str(source)[:32],
            source_uid=str(source_uid)[:128],
        )

    # Promote HIGH/CRITICAL runtime & WAF OCSF detections into SecurityFinding(Category.RUNTIME)
    src_low = str(source).lower()
    if norm_sev in ("CRITICAL", "HIGH") and (
        src_low in ("falco", "openappsec", "runtime") or str(category).upper() == "RUNTIME"
    ):
        try:
            from .risk import upsert_finding

            fp = SecurityFinding.build_fingerprint(
                SecurityFinding.Category.RUNTIME,
                getattr(service, "id", "platform"),
                src_low,
                str(title)[:120],
            )
            upsert_finding(
                fingerprint=fp,
                pillar=SecurityFinding.Pillar.XDR,
                category=SecurityFinding.Category.RUNTIME,
                severity=norm_sev,
                title=str(title)[:255],
                description=str(details or title)[:500],
                remediation="Inspect container process tree via DFIR Snapshot and quarantine or redeploy from a clean image.",
                service=service,
                deployment=deployment,
                server=server,
                emit_ocsf=False,
            )
        except Exception as exc:
            logger.debug("Runtime finding upsert from OCSF skipped: %s", exc)

    # Enforce auto_quarantine_on_critical_runtime policy when a CRITICAL runtime threat hits a service
    if norm_sev == "CRITICAL" and service is not None and (
        src_low in ("falco", "runtime") or str(category).upper() == "RUNTIME"
    ):
        try:
            from .models import SecurityPolicy
            from .xdr import execute_soar_action

            if SecurityPolicy.load().auto_quarantine_on_critical_runtime and getattr(service, "is_public", False):
                execute_soar_action(
                    "quarantine_service",
                    None,
                    service=service,
                    reason=f"Auto-quarantine on CRITICAL runtime event: {str(title)[:120]}",
                )
        except Exception as exc:
            logger.debug("Auto-quarantine on critical runtime skipped: %s", exc)

    return obj


def _resolve_activity_service_and_deployment(act: dict[str, Any]):
    """Best-effort link an activity from SecurityEventsView to its Service and Deployment."""
    try:
        from apps.deployments.models import Deployment, Service
    except Exception:
        return None, None

    svc = None
    dep = None
    raw = act.get("raw") if isinstance(act.get("raw"), dict) else {}

    dep_id = str(act.get("deployment_id") or raw.get("deployment_id") or "").strip()
    act_id = str(act.get("id") or "").strip()
    if not dep_id and act_id.startswith("trivy-"):
        parts = act_id.split("-")
        # trivy-<uuid5parts>-<cve>
        if len(parts) >= 6:
            dep_id = "-".join(parts[1:6])
    if dep_id:
        try:
            dep = Deployment.objects.select_related("service").filter(id=dep_id).first()
            if dep is not None:
                svc = dep.service
        except Exception:
            dep = None

    if svc is None:
        svc_id = str(act.get("service_id") or raw.get("service_id") or "").strip()
        if svc_id:
            try:
                svc = Service.objects.filter(id=svc_id).first()
            except Exception:
                svc = None

    if svc is None:
        target = str(act.get("target") or "").strip()
        cand_name = target
        if target.startswith("container:"):
            # "container: my-svc (proc: bash)"
            cand_name = target.split("container:", 1)[1].split("(", 1)[0].strip()
        if cand_name and cand_name not in ("host", "host-kernel", "waf", "reverse-proxy", "smsly-falco", "unknown"):
            svc = Service.objects.filter(name=cand_name).first()

    return svc, dep


def ingest_activity_list(activities: list[dict[str, Any]]) -> int:
    """Ingest activities produced by SecurityEventsView into NormalizedSecurityEvent."""
    ingested = 0
    for act in activities or []:
        if not isinstance(act, dict):
            continue
        source = str(act.get("source") or "unknown").lower()[:32]
        source_uid = str(act.get("id") or "")
        if not source_uid:
            source_uid = hashlib.sha256(
                f"{source}|{act.get('title')}|{act.get('timestamp')}".encode("utf-8")
            ).hexdigest()[:24]
        target = str(act.get("target") or "")
        actor_ip = target if target and (target.count(".") == 3 or ":" in target) and " " not in target else ""
        svc, dep = _resolve_activity_service_and_deployment(act)
        try:
            record_ocsf_event(
                source=source,
                source_uid=source_uid,
                title=str(act.get("title") or "Security Event")[:255],
                details=str(act.get("details") or ""),
                severity=str(act.get("severity") or "INFO"),
                target=target,
                actor_ip=actor_ip,
                service=svc,
                deployment=dep,
                occurred_at=_parse_timestamp(act.get("timestamp")),
                raw=act.get("raw") if isinstance(act.get("raw"), dict) else {"activity": act},
            )
            ingested += 1
        except Exception as exc:
            logger.debug("Skipping OCSF activity ingest for %s: %s", source_uid, exc)
    return ingested


def ingest_from_audit_and_alerts(hours: int = 24) -> int:
    """Normalize recent security-relevant AuditLogs & ResourceAlerts into OCSF events."""
    from datetime import timedelta
    from apps.core.models import AuditLog

    cutoff = timezone.now() - timedelta(hours=max(1, hours))
    ingested = 0

    sec_prefixes = (
        "FAIL2BAN", "CROWDSEC", "CRYPTD", "ENCRYPTION", "API_KEY",
        "WEBHOOK", "EDGE", "MESH", "SOAR", "SECURITY", "AUTO_ROLLBACK",
    )
    logs = AuditLog.objects.filter(timestamp__gte=cutoff).order_by("-timestamp")[:250]
    for entry in logs:
        act_upper = str(entry.action or "").upper()
        if not act_upper.startswith(sec_prefixes):
            continue
        meta = entry.metadata if isinstance(entry.metadata, dict) else {}
        svc_id = meta.get("service_id")
        svc = None
        if svc_id:
            try:
                from apps.deployments.models import Service
                svc = Service.objects.filter(id=svc_id).first()
            except Exception:
                svc = None
        sev = "HIGH" if any(k in act_upper for k in ("LOCKDOWN", "FAIL", "BAN", "QUARANTINE", "HIJACK")) else "INFO"
        src = "cryptd" if ("CRYPTD" in act_upper or "ENCRYPTION" in act_upper) else (
            "edge" if "EDGE" in act_upper else "auditd"
        )
        try:
            record_ocsf_event(
                source=src,
                source_uid=f"auditlog-{entry.id}",
                title=f"Audit: {entry.action}",
                details=f"Actor={entry.actor} Target={entry.target}",
                severity=sev,
                target=entry.target or "",
                actor_user=entry.actor or "",
                actor_ip=str(meta.get("ip") or ""),
                service=svc,
                occurred_at=entry.timestamp,
                raw={"action": entry.action, "metadata": meta},
            )
            ingested += 1
        except Exception as exc:
            logger.debug("AuditLog OCSF ingest failed for %s: %s", entry.id, exc)

    try:
        from apps.notifications.models import ResourceAlert

        alerts = (
            ResourceAlert.objects.select_related("service")
            .filter(created_at__gte=cutoff)
            .order_by("-created_at")[:100]
        )
        for alert in alerts:
            sev_raw = str(getattr(alert, "severity", "warning") or "warning").upper()
            sev = "CRITICAL" if sev_raw == "CRITICAL" else "MEDIUM"
            metric_name = str(getattr(alert, "metric", "") or getattr(alert, "alert_type", "") or "resource")
            try:
                record_ocsf_event(
                    source="resource_alert",
                    source_uid=f"resalert-{alert.id}",
                    title=f"Resource Alert ({metric_name}) on `{alert.service.name}`",
                    details=str(getattr(alert, "message", "") or "")[:400],
                    severity=sev,
                    target=alert.service.name,
                    service=alert.service,
                    occurred_at=alert.created_at,
                    raw={"metric": metric_name, "resolved": getattr(alert, "resolved", False)},
                )
                ingested += 1
            except Exception:
                pass
    except Exception as exc:
        logger.debug("ResourceAlert OCSF ingest skipped: %s", exc)

    return ingested


def get_mitre_matrix_summary(service_qs=None) -> list[dict[str, Any]]:
    """Compute MITRE ATT&CK coverage + live hit counts from OCSF events and Findings."""
    evt_qs = NormalizedSecurityEvent.objects.all()
    fnd_qs = SecurityFinding.objects.filter(status__in=[SecurityFinding.Status.OPEN, SecurityFinding.Status.ACKNOWLEDGED])
    if service_qs is not None:
        evt_qs = evt_qs.filter(service__in=service_qs)
        fnd_qs = fnd_qs.filter(service__in=service_qs)

    evt_counts: dict[str, int] = {}
    for row in evt_qs.values_list("mitre_technique", flat=True):
        if row:
            evt_counts[row] = evt_counts.get(row, 0) + 1

    fnd_counts: dict[str, int] = {}
    for row in fnd_qs.values_list("mitre_technique", flat=True):
        if row:
            fnd_counts[row] = fnd_counts.get(row, 0) + 1

    matrix = []
    for tech in MITRE_TECHNIQUE_CATALOG:
        tid = tech["technique_id"]
        events = evt_counts.get(tid, 0)
        findings = fnd_counts.get(tid, 0)
        matrix.append({
            "tactic": tech["tactic"],
            "technique_id": tid,
            "name": tech["name"],
            "coverage_status": "DETECTED" if (events + findings) > 0 else "MONITORED",
            "events_count": events,
            "open_findings_count": findings,
        })
    return matrix
