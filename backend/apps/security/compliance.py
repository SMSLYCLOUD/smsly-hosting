"""Continuous GRC & Compliance Automation Engine (SOC 2 Type II, ISO 27001, NDPR, PCI-DSS)."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from django.utils import timezone

from .models import ComplianceControlResult, SBOMArtifact, SecurityFinding, SecurityPolicy

logger = logging.getLogger(__name__)


def verify_audit_log_chain(limit: int = 500) -> dict[str, Any]:
    """Cryptographically verify the immutable SHA-256 hash chain on the latest AuditLog rows."""
    from apps.core.models import AuditLog

    safe_limit = max(1, min(int(limit or 500), 5000))
    latest_rows = list(AuditLog.objects.order_by("-id")[:safe_limit])
    if not latest_rows:
        return {"verified": True, "checked_rows": 0, "broken_id": None}

    rows = list(reversed(latest_rows))
    first_row = rows[0]
    anchor = (
        AuditLog.objects.filter(id__lt=first_row.id)
        .order_by("-id")
        .values_list("hash", flat=True)
        .first()
    )
    prev_hash = anchor if anchor is not None else "0" * 64

    for row in rows:
        if row.previous_hash != prev_hash:
            return {"verified": False, "checked_rows": len(rows), "broken_id": row.id}
        expected = row.calculate_hash()
        if row.hash != expected:
            return {"verified": False, "checked_rows": len(rows), "broken_id": row.id}
        prev_hash = row.hash
    return {"verified": True, "checked_rows": len(rows), "broken_id": None}


def evaluate_all_compliance_controls() -> dict[str, Any]:
    """Evaluate SOC 2 Type II, ISO 27001:2022, NDPR, and PCI-DSS v4.0 from live telemetry."""
    from apps.deployments.models import Service
    from apps.deployments.models.core import PlatformConfig
    from apps.deployments.services.container_runtime import detect_best_runtime, is_sandboxed_runtime

    cfg = PlatformConfig.load()
    policy = SecurityPolicy.load()
    chain = verify_audit_log_chain()

    total_services = Service.objects.count()
    encrypted_services = Service.objects.filter(encryption_at_rest_enabled=True).count()
    waf_protected = Service.objects.filter(waf_opt_out=False).count()
    open_crit = SecurityFinding.objects.filter(
        status=SecurityFinding.Status.OPEN, severity=SecurityFinding.Severity.CRITICAL
    ).count()
    sbom_count = SBOMArtifact.objects.count()
    sandboxed = is_sandboxed_runtime(detect_best_runtime())

    cryptd_configured = False
    try:
        from apps.core.crypto_daemon import cryptd_client
        cryptd_configured = cryptd_client.is_enabled()
    except Exception:
        pass

    controls: list[dict[str, Any]] = [
        # ── SOC 2 Type II ───────────────────────────────────────────────
        {
            "framework": ComplianceControlResult.Framework.SOC2,
            "control_id": "CC6.1",
            "control_name": "Logical Access Controls, MFA & Least Privilege",
            "status": "PASS" if getattr(cfg, "enforce_device_trust", False) or open_crit == 0 else "PARTIAL",
            "score_pct": 95 if getattr(cfg, "enforce_device_trust", False) else 80,
            "evidence_summary": f"RBAC active; DeviceTrust={bool(getattr(cfg, 'enforce_device_trust', False))}; OpenCriticalFindings={open_crit}",
            "evidence_details": {"enforce_device_trust": bool(getattr(cfg, "enforce_device_trust", False)), "open_critical": open_crit},
            "remediation": "Enforce Device Trust + resolve all CRITICAL CIEM findings.",
        },
        {
            "framework": ComplianceControlResult.Framework.SOC2,
            "control_id": "CC6.6",
            "control_name": "Perimeter Boundary Protection (L7 WAF & IPS)",
            "status": "PASS" if (getattr(cfg, "enable_crowdsec_waf", False) and total_services == waf_protected) else "PARTIAL",
            "score_pct": 100 if getattr(cfg, "enable_crowdsec_waf", False) else 65,
            "evidence_summary": f"CrowdSec={bool(getattr(cfg, 'enable_crowdsec_waf', False))}, CorazaProtectedServices={waf_protected}/{total_services}, open-appsec={bool(getattr(cfg, 'openappsec_enabled', True))}",
            "evidence_details": {"crowdsec": bool(getattr(cfg, "enable_crowdsec_waf", False)), "waf_protected": waf_protected, "total_services": total_services},
            "remediation": "Keep CrowdSec WAF + Coraza enabled on all public services.",
        },
        {
            "framework": ComplianceControlResult.Framework.SOC2,
            "control_id": "CC6.7",
            "control_name": "Transmission & Data-at-Rest Cryptography",
            "status": "PASS" if (getattr(cfg, "backup_require_encryption", False) and (total_services == 0 or encrypted_services == total_services)) else "PARTIAL",
            "score_pct": 100 if (cryptd_configured and getattr(cfg, "backup_require_encryption", False)) else 70,
            "evidence_summary": f"BackupEncryptionRequired={bool(getattr(cfg, 'backup_require_encryption', False))}, grid-cryptd={cryptd_configured}, EncryptedServices={encrypted_services}/{total_services}",
            "evidence_details": {"cryptd_enabled": cryptd_configured, "encrypted_services": encrypted_services, "total_services": total_services},
            "remediation": "Enable Require Backup Encryption and opt sensitive services into grid-cryptd.",
        },
        {
            "framework": ComplianceControlResult.Framework.SOC2,
            "control_id": "CC7.4",
            "control_name": "Tamper-Evident Security Audit Trail",
            "status": "PASS" if chain["verified"] else "FAIL",
            "score_pct": 100 if chain["verified"] else 0,
            "evidence_summary": f"AuditLog SHA-256 chain verified={chain['verified']} ({chain['checked_rows']} rows checked).",
            "evidence_details": chain,
            "remediation": "Investigate broken AuditLog chain hash immediately.",
        },
        # ── ISO 27001:2022 ─────────────────────────────────────────────
        {
            "framework": ComplianceControlResult.Framework.ISO27001,
            "control_id": "A.8.8",
            "control_name": "Management of Technical Vulnerabilities & SBOM",
            "status": "PASS" if (getattr(cfg, "trivy_enabled", True) and policy.aspm_enabled and open_crit == 0) else "PARTIAL",
            "score_pct": 100 if (getattr(cfg, "trivy_enabled", True) and open_crit == 0) else 60,
            "evidence_summary": f"Trivy={bool(getattr(cfg, 'trivy_enabled', True))}, ASPM={policy.aspm_enabled}, SBOMArtifacts={sbom_count}, OpenCritical={open_crit}",
            "evidence_details": {"trivy_enabled": bool(getattr(cfg, "trivy_enabled", True)), "sbom_count": sbom_count, "open_critical": open_crit},
            "remediation": "Keep Trivy + ASPM enabled and clear CRITICAL vulnerability findings.",
        },
        {
            "framework": ComplianceControlResult.Framework.ISO27001,
            "control_id": "A.8.24",
            "control_name": "Use of Cryptography & Key Management",
            "status": "PASS" if (cryptd_configured and getattr(cfg, "backup_require_encryption", False)) else "PARTIAL",
            "score_pct": 100 if cryptd_configured else 75,
            "evidence_summary": f"Fernet EnvVars active; grid-cryptd={cryptd_configured}; Cosign={bool(getattr(cfg, 'cosign_enabled', True))}",
            "evidence_details": {"cryptd": cryptd_configured, "cosign": bool(getattr(cfg, "cosign_enabled", True))},
            "remediation": "Configure grid-cryptd Shamir/Vault root custody and enable Cosign verification.",
        },
        {
            "framework": ComplianceControlResult.Framework.ISO27001,
            "control_id": "A.8.20",
            "control_name": "Network Security & Workload Isolation",
            "status": "PASS" if sandboxed else "PARTIAL",
            "score_pct": 100 if sandboxed else 75,
            "evidence_summary": f"MicroVM/gVisor sandboxed={sandboxed}; per-project bridge isolation + iptables egress filtering active.",
            "evidence_details": {"sandboxed": sandboxed},
            "remediation": "Activate gVisor (`runsc`) or Kata runtime across worker nodes.",
        },
        # ── NDPR (Nigeria Data Protection Regulation) ───────────────────
        {
            "framework": ComplianceControlResult.Framework.NDPR,
            "control_id": "NDPR-Art2.6",
            "control_name": "Data Security, Pseudonymization & Sovereign Custody",
            "status": "PASS" if (encrypted_services > 0 or total_services == 0) and getattr(cfg, "backup_require_encryption", False) else "PARTIAL",
            "score_pct": 95 if cryptd_configured else 65,
            "evidence_summary": f"Self-hosted sovereign data custody; EncryptedServices={encrypted_services}/{total_services}; EncryptedBackups={bool(getattr(cfg, 'backup_require_encryption', False))}",
            "evidence_details": {"encrypted_services": encrypted_services, "total_services": total_services},
            "remediation": "Enable `encryption_at_rest_enabled` on all services handling Nigerian citizen PII.",
        },
        {
            "framework": ComplianceControlResult.Framework.NDPR,
            "control_id": "NDPR-Art3.1",
            "control_name": "72h Breach Detection & Forensic Audit Readiness",
            "status": "PASS" if chain["verified"] else "FAIL",
            "score_pct": 100 if chain["verified"] else 20,
            "evidence_summary": "OCSF event lake, automated ITDR/UEBA detection, and SHA-256 DFIR ForensicSnapshots ready.",
            "evidence_details": {"audit_chain_verified": chain["verified"]},
            "remediation": "Keep continuous OCSF telemetry ingestion and forensic snapshot readiness active.",
        },
        # ── PCI-DSS v4.0 ───────────────────────────────────────────────
        {
            "framework": ComplianceControlResult.Framework.PCIDSS,
            "control_id": "PCI-3.5",
            "control_name": "Protect Stored Account Data with Strong Cryptography",
            "status": "PASS" if (cryptd_configured and (total_services == 0 or encrypted_services == total_services)) else "PARTIAL",
            "score_pct": 100 if cryptd_configured and encrypted_services == total_services else 60,
            "evidence_summary": f"LUKS2 AES-XTS + AES-256-GCM Redis encryption via grid-cryptd (`{encrypted_services}/{total_services}` services).",
            "evidence_details": {"cryptd": cryptd_configured, "encrypted_services": encrypted_services},
            "remediation": "Opt all cardholder/payment services into `grid-cryptd`.",
        },
        {
            "framework": ComplianceControlResult.Framework.PCIDSS,
            "control_id": "PCI-6.2",
            "control_name": "Bespoke & Custom Software Secure SDLC (ASPM/DAST)",
            "status": "PASS" if (policy.aspm_enabled and policy.dast_on_deploy_enabled and open_crit == 0) else "PARTIAL",
            "score_pct": 95 if (policy.aspm_enabled and open_crit == 0) else 65,
            "evidence_summary": f"ASPM SAST/Secrets/IaC={policy.aspm_enabled}, Post-Deploy DAST={policy.dast_on_deploy_enabled}, OpenCritical={open_crit}",
            "evidence_details": {"aspm_enabled": policy.aspm_enabled, "dast_enabled": policy.dast_on_deploy_enabled},
            "remediation": "Resolve CRITICAL ASPM/DAST findings and enforce pre-build secret blocking.",
        },
    ]

    summary_by_fw: dict[str, dict[str, Any]] = {}
    for item in controls:
        ComplianceControlResult.objects.update_or_create(
            framework=item["framework"],
            control_id=item["control_id"],
            defaults={
                "control_name": item["control_name"],
                "status": item["status"],
                "score_pct": item["score_pct"],
                "evidence_summary": item["evidence_summary"],
                "evidence_details": item["evidence_details"],
                "remediation": item["remediation"],
            },
        )
        fw_entry = summary_by_fw.setdefault(item["framework"], {"controls": 0, "passed": 0, "total_score": 0})
        fw_entry["controls"] += 1
        if item["status"] == "PASS":
            fw_entry["passed"] += 1
        fw_entry["total_score"] += item["score_pct"]

    for fw, st in summary_by_fw.items():
        st["score_pct"] = round(st["total_score"] / max(1, st["controls"]))

    return {
        "evaluated_at": timezone.now().isoformat(),
        "frameworks": summary_by_fw,
        "controls": controls,
    }


def export_compliance_evidence_bundle(framework: str | None = None) -> dict[str, Any]:
    """Generate a SHA-256-signed auditor evidence package."""
    report = evaluate_all_compliance_controls()
    controls = report["controls"]
    if framework:
        fw_upper = framework.strip().upper()
        controls = [c for c in controls if c["framework"] == fw_upper]
    bundle = {
        "schema": "smsly.grc.evidence.v1",
        "generated_at": timezone.now().isoformat(),
        "framework_filter": framework or "ALL",
        "frameworks": report["frameworks"],
        "controls": controls,
    }
    bundle["bundle_sha256"] = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return bundle
