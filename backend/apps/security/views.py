"""REST API Views for SMSLY Aegis / Aeon-Parity Security Command Center."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.core.models import AuditLog
from apps.deployments.models import Deployment, Service
from apps.deployments.views._helpers import EmptySerializer
from apps.teams.permissions import assert_can_write, get_team_q_filter

from .cnapp import (
    compute_attack_paths,
    inspect_llm_prompt,
    run_ai_spm_audit,
    run_ciem_audit,
    run_cspm_audit,
    run_dspm_audit,
    run_kspm_audit,
)
from .compliance import evaluate_all_compliance_controls, export_compliance_evidence_bundle
from .models import (
    ComplianceControlResult,
    ForensicSnapshot,
    NormalizedSecurityEvent,
    SBOMArtifact,
    SecurityFinding,
    SecurityPolicy,
)
from .ocsf import get_mitre_matrix_summary, ingest_from_audit_and_alerts, record_ocsf_event
from .xdr import execute_soar_action, match_threat_intel, run_itdr_ueba_scan

logger = logging.getLogger(__name__)


def _is_platform_admin(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.is_staff))


def _parse_limit(raw: Any, *, default: int = 100, max_limit: int = 300) -> int:
    try:
        val = int(raw if raw not in (None, "") else default)
    except (TypeError, ValueError):
        return default
    return max(1, min(val, max_limit))


def _parse_uuid(raw: Any) -> str | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return str(uuid.UUID(s))
    except (ValueError, TypeError, AttributeError):
        return None


def _parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    s = str(raw or "").strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off", ""):
        return False
    return bool(raw)


def _user_services_qs(request):
    if _is_platform_admin(request.user):
        return Service.objects.all()
    return Service.objects.filter(get_team_q_filter(request.user, prefix="", request=request)).distinct()


def _scoped_findings_qs(request):
    qs = SecurityFinding.objects.select_related("service", "deployment", "server")
    if _is_platform_admin(request.user):
        return qs
    return qs.filter(service__in=_user_services_qs(request))


def _scoped_events_qs(request):
    qs = NormalizedSecurityEvent.objects.select_related("service", "deployment", "server")
    if _is_platform_admin(request.user):
        return qs
    return qs.filter(service__in=_user_services_qs(request))


def _serialize_finding(f: SecurityFinding) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "pillar": f.pillar,
        "category": f.category,
        "severity": f.severity,
        "status": f.status,
        "risk_score": f.risk_score,
        "epss_score": f.epss_score,
        "internet_exposed": f.internet_exposed,
        "data_sensitive": f.data_sensitive,
        "privileged": f.privileged,
        "title": f.title,
        "description": f.description,
        "remediation": f.remediation,
        "package_name": f.package_name,
        "installed_version": f.installed_version,
        "fixed_version": f.fixed_version,
        "file_path": f.file_path,
        "line_number": f.line_number,
        "cve_id": f.cve_id,
        "cwe_id": f.cwe_id,
        "mitre_tactic": f.mitre_tactic,
        "mitre_technique": f.mitre_technique,
        "mitre_technique_name": f.mitre_technique_name,
        "service_id": str(f.service_id) if f.service_id else None,
        "service_name": getattr(f.service, "name", None) if f.service_id else None,
        "deployment_id": str(f.deployment_id) if f.deployment_id else None,
        "server_id": str(f.server_id) if f.server_id else None,
        "sla_due_at": f.sla_due_at.isoformat() if f.sla_due_at else None,
        "first_seen_at": f.first_seen_at.isoformat() if f.first_seen_at else None,
        "last_seen_at": f.last_seen_at.isoformat() if f.last_seen_at else None,
    }


def _serialize_event(ev: NormalizedSecurityEvent) -> dict[str, Any]:
    return {
        "id": str(ev.id),
        "source": ev.source,
        "source_uid": ev.source_uid,
        "category_uid": ev.category_uid,
        "category_name": ev.category_name,
        "class_uid": ev.class_uid,
        "class_name": ev.class_name,
        "severity_id": ev.severity_id,
        "severity": ev.severity,
        "title": ev.title,
        "details": ev.details,
        "target": ev.target,
        "actor_ip": ev.actor_ip,
        "actor_user": ev.actor_user,
        "mitre_tactic": ev.mitre_tactic,
        "mitre_technique": ev.mitre_technique,
        "mitre_technique_name": ev.mitre_technique_name,
        "service_id": str(ev.service_id) if ev.service_id else None,
        "service_name": getattr(ev.service, "name", None) if ev.service_id else None,
        "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
        "raw_ocsf": ev.raw_ocsf,
    }


class SecurityOverviewView(GenericAPIView):
    """GET /api/v1/security/overview/ — Unified AEON Core/CNAPP/ASPM/XDR Posture."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        raw_svc_id = (request.query_params.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        svcs = _user_services_qs(request)
        if svc_id:
            svcs = svcs.filter(id=svc_id)

        findings = _scoped_findings_qs(request)
        if svc_id:
            findings = findings.filter(service_id=svc_id)

        open_findings = findings.filter(
            status__in=[SecurityFinding.Status.OPEN, SecurityFinding.Status.ACKNOWLEDGED]
        )
        sev_counts = {
            row["severity"]: row["c"]
            for row in open_findings.values("severity").annotate(c=Count("id"))
        }
        pillar_counts = {
            row["pillar"]: row["c"]
            for row in open_findings.values("pillar").annotate(c=Count("id"))
        }

        total_svcs = svcs.count()
        enc_svcs = svcs.filter(encryption_at_rest_enabled=True).count()
        waf_svcs = svcs.filter(waf_opt_out=False).count()

        attack = compute_attack_paths(service_qs=svcs)
        mitre = get_mitre_matrix_summary(service_qs=None if _is_platform_admin(request.user) and not svc_id else svcs)

        sboms_qs = SBOMArtifact.objects.filter(service__in=svcs)
        policy = SecurityPolicy.load()

        crit = sev_counts.get("CRITICAL", 0)
        high = sev_counts.get("HIGH", 0)
        med = sev_counts.get("MEDIUM", 0)
        risk_index = min(100, max(5, crit * 20 + high * 10 + med * 4 + len(attack["toxic_combinations"]) * 8))

        data: dict[str, Any] = {
            "risk_score": risk_index,
            "posture_grade": "A" if risk_index < 20 else ("B" if risk_index < 45 else ("C" if risk_index < 70 else "D")),
            "open_findings": {
                "total": open_findings.count(),
                "critical": crit,
                "high": high,
                "medium": med,
                "low": sev_counts.get("LOW", 0),
                "by_pillar": pillar_counts,
            },
            "encryption_coverage": {
                "encrypted_services": enc_svcs,
                "total_services": total_svcs,
                "waf_protected_services": waf_svcs,
            },
            "sbom_summary": {
                "artifacts_count": sboms_qs.count(),
                "slsa_verified_count": sboms_qs.filter(provenance_verified=True).count(),
            },
            "toxic_combinations": attack["toxic_combinations"][:10],
            "mitre_summary": {
                "techniques_total": len(mitre),
                "techniques_detected": sum(1 for t in mitre if t["coverage_status"] == "DETECTED"),
                "matrix": mitre,
            },
            "ocsf_events_count": _scoped_events_qs(request).filter(service__in=svcs).count() if svc_id else _scoped_events_qs(request).count(),
            "policy": {
                "aspm_enabled": policy.aspm_enabled,
                "aspm_block_on_secrets": policy.aspm_block_on_secrets,
                "aspm_block_on_critical_sast": policy.aspm_block_on_critical_sast,
                "aspm_block_on_critical_iac": policy.aspm_block_on_critical_iac,
                "sbom_enabled": policy.sbom_enabled,
                "slsa_provenance_enabled": policy.slsa_provenance_enabled,
                "dast_on_deploy_enabled": policy.dast_on_deploy_enabled,
                "ai_prompt_firewall_enabled": policy.ai_prompt_firewall_enabled,
                "auto_quarantine_on_critical_runtime": policy.auto_quarantine_on_critical_runtime,
            },
        }
        if _is_platform_admin(request.user):
            data["threat_intel"] = match_threat_intel()
            try:
                from apps.core.crypto_daemon import cryptd_client
                from apps.deployments.models.core import PlatformConfig
                cfg = PlatformConfig.load()
                cryptd_st: dict[str, Any] = {"enabled": bool(cryptd_client.is_enabled()), "state": "INACTIVE"}
                if cryptd_st["enabled"]:
                    raw_st = cryptd_client.get_status()
                    if isinstance(raw_st, dict):
                        cryptd_st.update(raw_st)
                data["cryptd_status"] = cryptd_st
                data["engines"] = {
                    "openappsec_enabled": bool(getattr(cfg, "openappsec_enabled", True)),
                    "openappsec_mode": str(getattr(cfg, "openappsec_mode", "prevent") or "prevent"),
                    "crowdsec_enabled": bool(getattr(cfg, "enable_crowdsec_waf", True)),
                    "crowdsec_first_strike": bool(getattr(cfg, "crowdsec_first_strike_enabled", True)),
                    "cf_bouncer_enabled": bool(getattr(cfg, "crowdsec_cf_enabled", False)),
                    "coraza_protected_services": waf_svcs,
                    "total_services": total_svcs,
                    "trivy_enabled": bool(getattr(cfg, "trivy_enabled", True)),
                    "trivy_severity": str(getattr(cfg, "trivy_fail_on_severity", "CRITICAL") or "CRITICAL"),
                    "cosign_enabled": bool(getattr(cfg, "cosign_enabled", True)),
                }
            except Exception as exc:
                logger.debug("Overview engines/cryptd enrichment skipped: %s", exc)
            if ComplianceControlResult.objects.exists():
                fw_scores: dict[str, dict[str, int]] = {}
                for row in ComplianceControlResult.objects.all():
                    e = fw_scores.setdefault(row.framework, {"controls": 0, "passed": 0, "total_score": 0})
                    e["controls"] += 1
                    if row.status == ComplianceControlResult.Status.PASS:
                        e["passed"] += 1
                    e["total_score"] += row.score_pct
                data["compliance_frameworks"] = {
                    fw: {"controls": v["controls"], "passed": v["passed"], "score_pct": round(v["total_score"] / max(1, v["controls"]))}
                    for fw, v in fw_scores.items()
                }
            else:
                data["compliance_frameworks"] = {}
        return Response(data)


class SecurityFindingsListView(GenericAPIView):
    """GET /api/v1/security/findings/ and POST to run an on-demand audit sweep."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = _scoped_findings_qs(request)
        pillar = (request.query_params.get("pillar") or "").strip().upper()
        category = (request.query_params.get("category") or "").strip().upper()
        severity = (request.query_params.get("severity") or "").strip().upper()
        st = (request.query_params.get("status") or "").strip().upper()
        raw_svc_id = (request.query_params.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        q = (request.query_params.get("q") or "").strip()

        if pillar:
            qs = qs.filter(pillar=pillar)
        if category:
            qs = qs.filter(category=category)
        if severity:
            qs = qs.filter(severity=severity)
        if st and st != "ALL":
            qs = qs.filter(status=st)
        elif not st:
            qs = qs.filter(status__in=[SecurityFinding.Status.OPEN, SecurityFinding.Status.ACKNOWLEDGED])
        if svc_id:
            qs = qs.filter(service_id=svc_id)
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(package_name__icontains=q)
                | Q(cve_id__icontains=q)
                | Q(file_path__icontains=q)
            )

        limit = _parse_limit(request.query_params.get("limit"), default=100, max_limit=300)
        items = [_serialize_finding(f) for f in qs[:limit]]
        return Response({"count": qs.count(), "results": items})

    def post(self, request):
        """Run an immediate CNAPP/XDR/GRC sweep (scoped for users, full fleet for admins)."""
        svcs = _user_services_qs(request)
        raw_svc_id = (request.data.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        if svc_id:
            target_svc = svcs.filter(id=svc_id).first()
            if not target_svc:
                return Response({"error": "Service not found or access denied"}, status=status.HTTP_404_NOT_FOUND)
            assert_can_write(request.user, target_svc, action="run security scan")
            svcs = svcs.filter(id=svc_id)
        elif not _is_platform_admin(request.user):
            # Ensure non-admin callers have write permission on at least one scoped service
            # (blocks read-only Team VIEWERs from triggering DB-mutating scans).
            first_svc = svcs.first()
            if first_svc is not None:
                assert_can_write(request.user, first_svc, action="run security scan")

        results: dict[str, Any] = {
            "dspm": run_dspm_audit(service_qs=svcs),
            "kspm": run_kspm_audit(service_qs=svcs),
        }
        if _is_platform_admin(request.user) and not svc_id:
            results["cspm"] = run_cspm_audit()
            results["ciem"] = run_ciem_audit()
            results["ai_spm"] = run_ai_spm_audit()
            results["itdr"] = run_itdr_ueba_scan(hours=24)
            results["compliance"] = evaluate_all_compliance_controls()["frameworks"]
            results["ocsf_ingested"] = ingest_from_audit_and_alerts(hours=24)

        return Response({"status": "completed", "results": results})


class SecurityFindingDetailView(GenericAPIView):
    """PATCH /api/v1/security/findings/<uuid>/ — transition status with AuditLog."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, finding_id):
        parsed_id = _parse_uuid(finding_id)
        if not parsed_id:
            return Response({"error": "Invalid finding_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        finding = _scoped_findings_qs(request).filter(id=parsed_id).first()
        if finding is None:
            return Response({"error": "Finding not found"}, status=status.HTTP_404_NOT_FOUND)

        if finding.service is not None:
            assert_can_write(request.user, finding.service, action="update security finding")
        elif not _is_platform_admin(request.user):
            return Response({"error": "Admin access required for platform findings"}, status=status.HTTP_403_FORBIDDEN)

        new_status = str(request.data.get("status") or "").strip().upper()
        if new_status not in SecurityFinding.Status.values:
            return Response(
                {"error": f"Invalid status. Choose from {SecurityFinding.Status.values}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        prev_status = finding.status
        finding.status = new_status
        now = timezone.now()
        if new_status == SecurityFinding.Status.RESOLVED:
            finding.resolved_at = now
            finding.suppressed_until = None
        elif new_status in (SecurityFinding.Status.RISK_ACCEPTED, SecurityFinding.Status.FALSE_POSITIVE):
            finding.resolved_at = None
            sup_days = _parse_limit(request.data.get("suppress_days"), default=90, max_limit=365)
            finding.suppressed_until = now + timedelta(days=sup_days)
        else:
            finding.resolved_at = None
            finding.suppressed_until = None
        finding.save()

        try:
            AuditLog(
                user=request.user,
                actor=request.user.get_username(),
                action="SECURITY_FINDING_STATUS",
                target=finding.title[:255],
                metadata={
                    "finding_id": str(finding.id),
                    "service_id": str(finding.service_id or ""),
                    "from_status": prev_status,
                    "to_status": new_status,
                },
            ).save()
        except Exception:
            pass

        return Response(_serialize_finding(finding))


class SecurityOcsfEventsView(GenericAPIView):
    """GET /api/v1/security/events/ — Query normalized OCSF v1.1 telemetry lake."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = _scoped_events_qs(request)
        source = (request.query_params.get("source") or "").strip().lower()
        sev = (request.query_params.get("severity") or "").strip().upper()
        tech = (request.query_params.get("mitre_technique") or "").strip().upper()
        raw_svc_id = (request.query_params.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)

        if source and source != "all":
            qs = qs.filter(source=source)
        if sev and sev != "ALL":
            qs = qs.filter(severity=sev)
        if tech:
            qs = qs.filter(mitre_technique=tech)
        if svc_id:
            qs = qs.filter(service_id=svc_id)

        limit = _parse_limit(request.query_params.get("limit"), default=100, max_limit=300)
        return Response({
            "schema": "OCSF 1.1.0",
            "count": qs.count(),
            "results": [_serialize_event(ev) for ev in qs[:limit]],
        })

    def post(self, request):
        if not _is_platform_admin(request.user):
            return Response({"error": "Staff access required"}, status=status.HTTP_403_FORBIDDEN)
        count = ingest_from_audit_and_alerts(hours=24)
        return Response({"ingested": count})


class SecurityAttackPathsView(GenericAPIView):
    """GET /api/v1/security/attack-paths/ — Risk Fusion graph & Toxic Combinations."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        svcs = _user_services_qs(request)
        raw_svc_id = (request.query_params.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        if svc_id:
            svcs = svcs.filter(id=svc_id)
        return Response(compute_attack_paths(service_qs=svcs))


class SecurityMitreView(GenericAPIView):
    """GET /api/v1/security/mitre/ — MITRE ATT&CK Coverage Matrix."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        svcs = _user_services_qs(request)
        raw_svc_id = (request.query_params.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        if svc_id:
            svcs = svcs.filter(id=svc_id)
        matrix = get_mitre_matrix_summary(
            service_qs=None if _is_platform_admin(request.user) and not svc_id else svcs
        )
        return Response({"techniques": matrix})


class SecuritySbomView(GenericAPIView):
    """GET /api/v1/security/sbom/ — CycloneDX v1.5 SBOMs & Cross-Service Package Search."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        svcs = _user_services_qs(request)
        raw_svc_id = (request.query_params.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        pkg_query = (request.query_params.get("package") or "").strip().lower()
        raw_dep_id = (request.query_params.get("deployment_id") or "").strip()
        dep_id = _parse_uuid(raw_dep_id)
        if raw_dep_id and not dep_id:
            return Response({"error": "Invalid deployment_id UUID"}, status=status.HTTP_400_BAD_REQUEST)

        qs = SBOMArtifact.objects.filter(service__in=svcs).select_related("service", "deployment")
        if not dep_id:
            qs = qs.defer("sbom_document")
        if svc_id:
            qs = qs.filter(service_id=svc_id)
        if dep_id:
            qs = qs.filter(deployment_id=dep_id)

        results = []
        for art in qs[:100]:
            matched_pkgs = art.packages or []
            if pkg_query:
                matched_pkgs = [
                    p for p in matched_pkgs
                    if pkg_query in str(p.get("name", "")).lower() or pkg_query in str(p.get("purl", "")).lower()
                ]
                if not matched_pkgs:
                    continue
            results.append({
                "id": str(art.id),
                "service_id": str(art.service_id),
                "service_name": art.service.name,
                "deployment_id": str(art.deployment_id),
                "sbom_format": art.sbom_format,
                "spec_version": art.spec_version,
                "image_ref": art.image_ref,
                "image_digest": art.image_digest,
                "package_count": art.package_count,
                "provenance_verified": art.provenance_verified,
                "slsa_provenance": art.slsa_provenance,
                "matched_packages": matched_pkgs[:50],
                "sbom_document": art.sbom_document if dep_id else None,
                "created_at": art.created_at.isoformat(),
            })
        return Response({"count": len(results), "results": results})


class SecurityComplianceView(GenericAPIView):
    """GET/POST /api/v1/security/compliance/ — SOC 2, ISO 27001, NDPR, PCI-DSS GRC controls."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not _is_platform_admin(request.user):
            return Response({"error": "Admin access required for platform GRC reports"}, status=status.HTTP_403_FORBIDDEN)
        fmt = (request.query_params.get("export") or "").strip().lower()
        fw = (request.query_params.get("framework") or "").strip() or None
        if fmt == "json":
            return Response(export_compliance_evidence_bundle(framework=fw))
        report = evaluate_all_compliance_controls()
        if fw:
            fw_u = fw.upper()
            report["controls"] = [c for c in report["controls"] if c["framework"] == fw_u]
        return Response(report)


class SecurityPolicyView(GenericAPIView):
    """GET/PATCH /api/v1/security/policy/ — Admin control over ASPM/XDR enforcement gates."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        p = SecurityPolicy.load()
        return Response({
            "aspm_enabled": p.aspm_enabled,
            "aspm_block_on_secrets": p.aspm_block_on_secrets,
            "aspm_block_on_critical_sast": p.aspm_block_on_critical_sast,
            "aspm_block_on_critical_iac": p.aspm_block_on_critical_iac,
            "sbom_enabled": p.sbom_enabled,
            "slsa_provenance_enabled": p.slsa_provenance_enabled,
            "dast_on_deploy_enabled": p.dast_on_deploy_enabled,
            "ai_prompt_firewall_enabled": p.ai_prompt_firewall_enabled,
            "auto_quarantine_on_critical_runtime": p.auto_quarantine_on_critical_runtime,
        })

    def patch(self, request):
        p = SecurityPolicy.load()
        bool_fields = (
            "aspm_enabled", "aspm_block_on_secrets", "aspm_block_on_critical_sast",
            "aspm_block_on_critical_iac", "sbom_enabled", "slsa_provenance_enabled",
            "dast_on_deploy_enabled", "ai_prompt_firewall_enabled",
            "auto_quarantine_on_critical_runtime",
        )
        updated = []
        for field in bool_fields:
            if field in request.data:
                setattr(p, field, _parse_bool(request.data[field]))
                updated.append(field)
        if updated:
            p.save()
        return self.get(request)


class SecuritySoarExecuteView(GenericAPIView):
    """POST /api/v1/security/soar/execute/ — Execute SOAR Containment / DFIR Playbook."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        action = str(request.data.get("action") or "").strip().lower()
        raw_svc_id = (request.data.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        ip = str(request.data.get("ip") or "").strip()
        reason = str(request.data.get("reason") or "").strip()

        if action == "ban_ip" and not _is_platform_admin(request.user):
            return Response({"error": "Only platform administrators can issue global IP bans"}, status=status.HTTP_403_FORBIDDEN)

        service = None
        if svc_id:
            service = _user_services_qs(request).filter(id=svc_id).first()
            if service is None:
                return Response({"error": "Service not found or access denied"}, status=status.HTTP_404_NOT_FOUND)
            assert_can_write(request.user, service, action=f"execute SOAR {action}")
        elif action != "ban_ip":
            return Response({"error": "service_id is required for this SOAR action"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            res = execute_soar_action(action, request.user, service=service, ip=ip, reason=reason)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(res, status=status.HTTP_200_OK if res.get("ok") else status.HTTP_502_BAD_GATEWAY)


class SecurityForensicsListView(GenericAPIView):
    """GET /api/v1/security/forensics/ — List SHA-256-signed DFIR bundles."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        svcs = _user_services_qs(request)
        raw_svc_id = (request.query_params.get("service_id") or "").strip()
        svc_id = _parse_uuid(raw_svc_id)
        if raw_svc_id and not svc_id:
            return Response({"error": "Invalid service_id UUID"}, status=status.HTTP_400_BAD_REQUEST)
        qs = ForensicSnapshot.objects.filter(service__in=svcs).select_related("service")
        if svc_id:
            qs = qs.filter(service_id=svc_id)
        items = [
            {
                "id": str(s.id),
                "service_id": str(s.service_id),
                "service_name": s.service.name,
                "container_id": s.container_id,
                "trigger": s.trigger,
                "captured_by": s.captured_by,
                "sha256_digest": s.sha256_digest,
                "bundle": s.bundle,
                "created_at": s.created_at.isoformat(),
            }
            for s in qs[:50]
        ]
        return Response({"count": qs.count(), "results": items})


class SecurityAiHuntView(GenericAPIView):
    """POST /api/v1/security/ai-hunt/ — Cortex AI-parity Natural Language Threat Hunter."""

    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        raw_query = str(request.data.get("query") or "").strip()
        if not raw_query:
            return Response({"error": "query is required"}, status=status.HTTP_400_BAD_REQUEST)

        policy = SecurityPolicy.load()
        guard = inspect_llm_prompt(raw_query)
        if guard["injection_detected"]:
            try:
                import hashlib

                actor = request.user.get_username() if request.user and request.user.is_authenticated else "anonymous"
                uid_hash = hashlib.sha256(f"{actor}|{raw_query[:120]}".encode("utf-8")).hexdigest()[:20]
                record_ocsf_event(
                    source="ai_spm",
                    source_uid=f"ai-inj-{uid_hash}",
                    title=f"AI-SPM Prompt Injection Blocked ({actor})",
                    details=f"Matched rules: {', '.join(guard['injection_rules'][:3])}",
                    severity="HIGH",
                    target="ai-threat-hunter",
                    actor_user=actor,
                    category="AI_SPM",
                )
            except Exception:
                pass
            if policy.ai_prompt_firewall_enabled:
                return Response(
                    {
                        "blocked_by_ai_spm": True,
                        "reason": "AI-SPM Prompt Firewall blocked potential prompt-injection pattern.",
                        "matched_rules": guard["injection_rules"],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        sanitized_q = guard["sanitized_prompt"]
        svcs = _user_services_qs(request)
        open_findings = list(
            _scoped_findings_qs(request)
            .filter(status__in=[SecurityFinding.Status.OPEN, SecurityFinding.Status.ACKNOWLEDGED])
            .order_by("-risk_score")[:10]
        )
        recent_events = list(_scoped_events_qs(request).order_by("-occurred_at")[:10])
        attack = compute_attack_paths(service_qs=svcs)

        matching_findings = [
            _serialize_finding(f) for f in open_findings
            if any(tok in f"{f.title} {f.category} {f.package_name} {f.cve_id} {getattr(f.service, 'name', '')}".lower()
                   for tok in sanitized_q.lower().split() if len(tok) > 2)
        ] or [_serialize_finding(f) for f in open_findings[:5]]

        summary_context = (
            f"Services={svcs.count()}, OpenCriticalHighFindings={len(open_findings)}, "
            f"ToxicPaths={len(attack['toxic_combinations'])}, RecentOCSF={len(recent_events)}.\n"
            f"Top findings: {[f'{f.severity}:{f.title}' for f in open_findings[:5]]}"
        )

        answer = ""
        try:
            from apps.intelligence.providers import ask_with_fallback
            prompt = (
                "You are SMSLY Aegis Security Analyst. Answer concisely using ONLY this verified platform telemetry.\n"
                f"Telemetry:\n{summary_context}\n\nQuestion: {sanitized_q}"
            )
            ai_res, _provider = ask_with_fallback(prompt, mode="fast")
            if ai_res and isinstance(ai_res, str) and len(ai_res.strip()) > 15:
                answer = ai_res.strip()
        except Exception:
            answer = ""

        if not answer:
            top_line = (
                f"Highest-risk item: `{open_findings[0].title}` (risk_score={open_findings[0].risk_score})."
                if open_findings
                else "No open high-risk security findings across your scoped services."
            )
            answer = (
                f"Evaluated {svcs.count()} scoped service(s), {len(recent_events)} recent OCSF events, "
                f"and {len(attack['toxic_combinations'])} toxic attack path(s). {top_line}"
            )

        return Response({
            "query": sanitized_q,
            "redacted_types": guard["redacted_types"],
            "answer": answer,
            "matched_findings": matching_findings[:10],
            "toxic_combinations": attack["toxic_combinations"][:5],
        })
