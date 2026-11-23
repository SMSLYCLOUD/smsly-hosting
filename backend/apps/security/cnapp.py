"""CNAPP Engine: CSPM, KSPM, CIEM, DSPM, AI-SPM, and Attack-Path Risk Fusion."""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from django.db.models import Count, Q
from django.utils import timezone

from .models import SecurityFinding, SecurityPolicy
from .risk import resolve_stale_findings, upsert_finding

logger = logging.getLogger(__name__)

_SENSITIVE_ADDON_TYPES = frozenset({
    "POSTGRES", "MYSQL", "MARIADB", "COCKROACHDB", "TIMESCALEDB", "PERCONA",
    "MONGODB", "COUCHDB", "REDIS", "VALKEY", "KEYDB", "DRAGONFLYDB",
    "MINIO", "SEAWEEDFS", "VAULT", "KEYCLOAK",
})

_SECRET_ENV_RE = re.compile(r"(SECRET|TOKEN|PASSWORD|PRIVATE_KEY|API_KEY|DSN|CREDENTIAL)", re.I)

_PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.I),
    re.compile(r"reveal\s+(?:your\s+)?(?:system\s+prompt|hidden\s+instructions|api\s+key)", re.I),
    re.compile(r"\b(?:DAN|jailbreak)\s+mode\b", re.I),
    re.compile(r"execute\s+(?:shell|bash|os\.system|subprocess)", re.I),
)

_PII_REDACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("BEARER_TOKEN", re.compile(r"\b(?:ghp|gho|sk_live|sk-|AIza)[A-Za-z0-9_-]{16,}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARD", re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b")),
)


def run_cspm_audit() -> dict[str, Any]:
    """Audit ManagedServer fleet, PlatformConfig posture, and CloudProviders (CSPM)."""
    from apps.deployments.models.core import ManagedServer, PlatformConfig

    cfg = PlatformConfig.load()
    active_fps: set[str] = set()
    created_or_updated = 0

    def _record(key: str, sev: str, title: str, desc: str, rem: str, server=None) -> None:
        nonlocal created_or_updated
        fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.CSPM, key)
        active_fps.add(fp)
        upsert_finding(
            fingerprint=fp,
            pillar=SecurityFinding.Pillar.CNAPP,
            category=SecurityFinding.Category.CSPM,
            severity=sev,
            title=title,
            description=desc,
            remediation=rem,
            server=server,
            internet_exposed=True,
            privileged=True,
            emit_ocsf=sev in ("CRITICAL", "HIGH"),
        )
        created_or_updated += 1

    if not getattr(cfg, "enable_crowdsec_waf", False):
        _record(
            "platform:crowdsec_disabled",
            "HIGH",
            "CrowdSec IPS/WAF is disabled platform-wide",
            "Perimeter CrowdSec behavioral blocking and first-strike ban feeds are inactive.",
            "Enable CrowdSec WAF under Settings -> Security Scanning and configure a LAPI bouncer token.",
        )

    if not getattr(cfg, "backup_require_encryption", False):
        _record(
            "platform:backup_unencrypted",
            "HIGH",
            "Mandatory backup encryption is disabled",
            "Server and service backups may be generated without mandatory cryptographic wrapping.",
            "Enable `Require Backup Encryption` under Platform Security settings.",
        )

    domain = (getattr(cfg, "domain", "") or "").strip()
    if domain and not getattr(cfg, "use_ssl", False):
        _record(
            "platform:ssl_disabled",
            "MEDIUM",
            f"Automatic HTTPS (use_ssl) is disabled for platform domain `{domain}`",
            "Control-plane traffic may accept plaintext HTTP without enforced TLS termination.",
            "Enable `USE_SSL=true` in PlatformConfig / Domain Settings.",
        )

    for srv in ManagedServer.objects.all():
        if not getattr(srv, "verify_tls", True):
            _record(
                f"server:{srv.id}:insecure_tls",
                "HIGH",
                f"Node `{srv.name}` skips inter-node TLS certificate verification",
                f"ManagedServer {srv.name} ({srv.host}) has verify_tls=False, risking MITM on node orchestration.",
                "Enable `verify_tls=True` and pin `tls_cert_sha256` on the server record.",
                server=srv,
            )
        elif not (getattr(srv, "tls_cert_sha256", "") or "").strip():
            _record(
                f"server:{srv.id}:unpinned_tls",
                "LOW",
                f"Node `{srv.name}` has no SHA-256 TLS certificate pin",
                f"ManagedServer {srv.name} trusts CA validation without explicit leaf SHA-256 pinning.",
                "Set `tls_cert_sha256` to enforce zero-trust peer certificate pinning.",
                server=srv,
            )

    resolved = resolve_stale_findings(category=SecurityFinding.Category.CSPM, active_fingerprints=active_fps)
    return {"checked": True, "active": created_or_updated, "resolved": resolved}


def run_kspm_audit(service_qs=None) -> dict[str, Any]:
    """Audit container runtime sandboxing, WAF bypasses, and network segmentation (KSPM)."""
    from apps.deployments.models import Service
    from apps.deployments.services.container_runtime import detect_best_runtime, is_sandboxed_runtime

    active_fps: set[str] = set()
    findings_count = 0

    runtime = detect_best_runtime() if service_qs is None else "scoped"
    if service_qs is None and not is_sandboxed_runtime(runtime):
        fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.KSPM, "runtime:runc")
        active_fps.add(fp)
        upsert_finding(
            fingerprint=fp,
            pillar=SecurityFinding.Pillar.CNAPP,
            category=SecurityFinding.Category.KSPM,
            severity="MEDIUM",
            title=f"Containers run on shared-kernel runtime `{runtime}` instead of gVisor/Kata",
            description="MicroVM (Kata) or user-space kernel (gVisor runsc) provides stronger tenant boundary isolation.",
            remediation="Install gVisor (`runsc`) or Kata runtime on compute nodes and run `reconcile_gvisor_hosts_task`.",
            privileged=True,
        )
        findings_count += 1

    if service_qs is None:
        qs = Service.objects.all()
    elif hasattr(service_qs, "all"):
        qs = service_qs.all()
    else:
        qs = service_qs
    resolved = 0
    for svc in qs:
        svc_fps: set[str] = set()
        if getattr(svc, "waf_opt_out", False) and getattr(svc, "is_public", True):
            fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.KSPM, "svc:waf_opt_out", svc.id)
            active_fps.add(fp)
            svc_fps.add(fp)
            upsert_finding(
                fingerprint=fp,
                pillar=SecurityFinding.Pillar.CNAPP,
                category=SecurityFinding.Category.KSPM,
                severity="HIGH",
                title=f"Public service `{svc.name}` has opted out of Coraza L7 WAF",
                description="Coraza OWASP CRS protection is disabled for this publicly routed workload.",
                remediation="Disable `waf_opt_out` in service Scaling/Security settings unless an upstream WAF is verified.",
                service=svc,
                internet_exposed=True,
            )
            findings_count += 1
        if service_qs is not None:
            resolved += resolve_stale_findings(
                category=SecurityFinding.Category.KSPM,
                active_fingerprints=svc_fps,
                service=svc,
            )

    if service_qs is None:
        resolved = resolve_stale_findings(category=SecurityFinding.Category.KSPM, active_fingerprints=active_fps)
    return {"checked": True, "runtime": runtime, "active": findings_count, "resolved": resolved}


def run_ciem_audit() -> dict[str, Any]:
    """Audit dormant/over-privileged APITokens and Team RBAC sprawl (CIEM)."""
    from django.contrib.auth import get_user_model
    from apps.core.models import APIToken

    now = timezone.now()
    dormant_cutoff = now - timedelta(days=30)
    never_used_cutoff = now - timedelta(days=14)
    active_fps: set[str] = set()
    count = 0

    for tok in APIToken.objects.filter(is_active=True).select_related("user"):
        is_dormant = (
            (tok.last_used_at is not None and tok.last_used_at < dormant_cutoff)
            or (tok.last_used_at is None and tok.created_at < never_used_cutoff)
        )
        if is_dormant:
            fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.CIEM, "dormant_token", tok.id)
            active_fps.add(fp)
            upsert_finding(
                fingerprint=fp,
                pillar=SecurityFinding.Pillar.CNAPP,
                category=SecurityFinding.Category.CIEM,
                severity="MEDIUM",
                title=f"Dormant active API token `{tok.name}` ({tok.prefix}...)",
                description=f"Token owned by `{tok.user.get_username()}` has not been used in >30 days (or 14d since creation).",
                remediation="Revoke unused API tokens in Settings -> API Keys to reduce credential exposure.",
                privileged=not bool(tok.scopes),
            )
            count += 1

        if not tok.scopes and not tok.projects:
            fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.CIEM, "wildcard_token", tok.id)
            active_fps.add(fp)
            upsert_finding(
                fingerprint=fp,
                pillar=SecurityFinding.Pillar.CNAPP,
                category=SecurityFinding.Category.CIEM,
                severity="HIGH",
                title=f"Unscoped full-access API token `{tok.name}` ({tok.prefix}...)",
                description=f"Token `{tok.name}` grants unrestricted read/write access across all projects (`scopes=[]`, `projects=[]`).",
                remediation="Re-issue the token with least-privilege `scopes` (`['read']`) and explicit `projects` UUID constraints.",
                privileged=True,
                emit_ocsf=True,
            )
            count += 1

    User = get_user_model()
    superuser_count = User.objects.filter(is_superuser=True, is_active=True).count()
    if superuser_count > 3:
        fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.CIEM, "superuser_sprawl")
        active_fps.add(fp)
        upsert_finding(
            fingerprint=fp,
            pillar=SecurityFinding.Pillar.CNAPP,
            category=SecurityFinding.Category.CIEM,
            severity="MEDIUM",
            title=f"Elevated superuser count ({superuser_count} active platform superusers)",
            description="More than 3 active Django superuser accounts exist across the control plane.",
            remediation="Demote non-breakglass administrators to scoped Team ADMIN/MEMBER roles.",
            privileged=True,
        )
        count += 1

    resolved = resolve_stale_findings(category=SecurityFinding.Category.CIEM, active_fingerprints=active_fps)
    return {"checked": True, "active": count, "resolved": resolved}


def run_dspm_audit(service_qs=None) -> dict[str, Any]:
    """Audit Data Security Posture: unencrypted data addons, public buckets, unmasked secret EnvVars."""
    from apps.deployments.models import Addon, EnvironmentVariable, Service

    services = service_qs if service_qs is not None else Service.objects.all()
    active_fps: set[str] = set()
    count = 0
    resolved = 0

    for svc in services.prefetch_related("addons", "env_vars"):
        svc_fps: set[str] = set()
        sensitive_addons = [
            a for a in svc.addons.all()
            if str(a.addon_type or "").upper() in _SENSITIVE_ADDON_TYPES
            and a.status not in ("DELETED", "DELETION_PENDING")
        ]
        if sensitive_addons and not getattr(svc, "encryption_at_rest_enabled", False):
            addon_names = ", ".join(f"{a.addon_type}:{a.name}" for a in sensitive_addons[:4])
            fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.DSPM, "unencrypted_service_addons", svc.id)
            active_fps.add(fp)
            svc_fps.add(fp)
            upsert_finding(
                fingerprint=fp,
                pillar=SecurityFinding.Pillar.CNAPP,
                category=SecurityFinding.Category.DSPM,
                severity="HIGH",
                title=f"Service `{svc.name}` stores sensitive data without `grid-cryptd` encryption-at-rest",
                description=f"Attached datastores ({addon_names}) lack per-service HKDF SEK encryption, Redis GRD1 ciphertext, and DB point-of-contact lock protection.",
                remediation="Enable Per-Service Encryption at Rest in the service Overview/Settings panel.",
                service=svc,
                data_sensitive=True,
                privileged=True,
                emit_ocsf=True,
            )
            count += 1

        for addon in sensitive_addons:
            if getattr(addon, "is_bucket_public", False):
                fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.DSPM, "public_bucket", addon.id)
                active_fps.add(fp)
                svc_fps.add(fp)
                upsert_finding(
                    fingerprint=fp,
                    pillar=SecurityFinding.Pillar.CNAPP,
                    category=SecurityFinding.Category.DSPM,
                    severity="CRITICAL",
                    title=f"Object storage bucket `{addon.name}` allows public unauthenticated access",
                    description=f"Addon {addon.addon_type} ({addon.name}) on service `{svc.name}` has is_bucket_public=True.",
                    remediation="Toggle public bucket access OFF immediately or move only non-sensitive static assets behind CDN signed URLs.",
                    service=svc,
                    internet_exposed=True,
                    data_sensitive=True,
                    emit_ocsf=True,
                )
                count += 1

        for ev in svc.env_vars.all():
            if not getattr(ev, "is_secret", False) and _SECRET_ENV_RE.search(str(ev.key or "")):
                fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.DSPM, "unmarked_secret_env", svc.id, ev.key)
                active_fps.add(fp)
                svc_fps.add(fp)
                upsert_finding(
                    fingerprint=fp,
                    pillar=SecurityFinding.Pillar.CNAPP,
                    category=SecurityFinding.Category.DSPM,
                    severity="LOW",
                    title=f"Secret-like EnvVar `{ev.key}` on `{svc.name}` is not marked `is_secret=True`",
                    description="Marking sensitive EnvVars with `is_secret=True` prevents accidental display in UI previews and review summaries.",
                    remediation=f"Set `is_secret=True` on `{ev.key}` under Service -> Environment Variables.",
                    service=svc,
                    data_sensitive=True,
                )
                count += 1

        if service_qs is not None:
            resolved += resolve_stale_findings(
                category=SecurityFinding.Category.DSPM,
                active_fingerprints=svc_fps,
                service=svc,
            )

    if service_qs is None:
        resolved = resolve_stale_findings(category=SecurityFinding.Category.DSPM, active_fingerprints=active_fps)
    return {"checked": True, "active": count, "resolved": resolved}


def inspect_llm_prompt(prompt: str) -> dict[str, Any]:
    """AI-SPM prompt injection & outbound PII/secret redaction guard."""
    text = str(prompt or "")
    matched_injections = [p.pattern for p in _PROMPT_INJECTION_PATTERNS if p.search(text)]
    redacted_text = text
    redactions: list[str] = []
    for label, pattern in _PII_REDACTION_PATTERNS:
        if pattern.search(redacted_text):
            redactions.append(label)
            redacted_text = pattern.sub(f"[REDACTED:{label}]", redacted_text)
    return {
        "safe": not matched_injections and not redactions,
        "injection_detected": bool(matched_injections),
        "injection_rules": matched_injections,
        "redacted_types": redactions,
        "sanitized_prompt": redacted_text,
    }


def run_ai_spm_audit() -> dict[str, Any]:
    """Audit deployed AI services and external LLM provider security configuration (AI-SPM)."""
    active_fps: set[str] = set()
    count = 0

    try:
        from apps.intelligence.models import AIProviderSettings
        cfg = AIProviderSettings.objects.first()
    except Exception:
        cfg = None

    policy = SecurityPolicy.load()
    if not policy.ai_prompt_firewall_enabled:
        fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.AI_SPM, "prompt_firewall_disabled")
        active_fps.add(fp)
        upsert_finding(
            fingerprint=fp,
            pillar=SecurityFinding.Pillar.CNAPP,
            category=SecurityFinding.Category.AI_SPM,
            severity="MEDIUM",
            title="AI-SPM Prompt Injection & PII Firewall is disabled",
            description="Outbound LLM prompts may contain unredacted credentials or PII.",
            remediation="Enable `ai_prompt_firewall_enabled` in SecurityPolicy.",
        )
        count += 1

    if cfg is not None:
        custom_url = (getattr(cfg, "localllm_base_url", "") or "").strip()
        if custom_url.startswith("http://"):
            fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.AI_SPM, "plaintext_localllm")
            active_fps.add(fp)
            upsert_finding(
                fingerprint=fp,
                pillar=SecurityFinding.Pillar.CNAPP,
                category=SecurityFinding.Category.AI_SPM,
                severity="MEDIUM",
                title="Custom LLM endpoint uses unencrypted http:// transport",
                description=f"Local/custom LLM URL `{custom_url}` transmits prompts over plaintext HTTP.",
                remediation="Terminate TLS (`https://`) in front of internal LLM routers.",
            )
            count += 1

    resolved = resolve_stale_findings(category=SecurityFinding.Category.AI_SPM, active_fingerprints=active_fps)
    return {"checked": True, "active": count, "resolved": resolved}


def compute_attack_paths(service_qs=None) -> dict[str, Any]:
    """Build Risk Fusion attack-path graph connecting Public Ingress -> Service -> Sensitive Data."""
    from apps.deployments.models import Service

    services = (service_qs if service_qs is not None else Service.objects.all()).prefetch_related("addons", "security_findings")
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    toxic_combinations: list[dict[str, Any]] = []

    for svc in services[:100]:
        open_findings = [
            f for f in svc.security_findings.all()
            if f.status in (SecurityFinding.Status.OPEN, SecurityFinding.Status.ACKNOWLEDGED)
        ]
        max_risk = max((f.risk_score for f in open_findings), default=10)
        crit_or_high = [f for f in open_findings if f.severity in ("CRITICAL", "HIGH")]
        exposed = bool(getattr(svc, "is_public", True) and (getattr(svc, "public_domain", "") or getattr(svc, "custom_domains", None)))
        addons = [
            a for a in svc.addons.all()
            if str(a.addon_type or "").upper() in _SENSITIVE_ADDON_TYPES and a.status != "DELETED"
        ]
        encrypted = bool(getattr(svc, "encryption_at_rest_enabled", False))
        waf_off = bool(getattr(svc, "waf_opt_out", False))

        svc_node_id = f"svc:{svc.id}"
        nodes.append({
            "id": svc_node_id,
            "type": "service",
            "label": svc.name,
            "risk_score": max_risk,
            "internet_exposed": exposed,
            "encrypted_at_rest": encrypted,
            "open_findings": len(open_findings),
        })

        if exposed:
            ing_id = f"ingress:{svc.id}"
            domain_lbl = svc.public_domain or (svc.custom_domains[0] if svc.custom_domains else "public")
            nodes.append({"id": ing_id, "type": "ingress", "label": domain_lbl, "risk_score": 30 if waf_off else 15})
            edges.append({"source": ing_id, "target": svc_node_id, "relation": "EXPOSES_HTTP"})

        for addon in addons:
            db_id = f"addon:{addon.id}"
            db_risk = 85 if addon.is_bucket_public else (65 if not encrypted else 20)
            nodes.append({
                "id": db_id,
                "type": "datastore",
                "label": f"{addon.addon_type} ({addon.name})",
                "risk_score": db_risk,
                "encrypted": encrypted,
                "public_bucket": bool(addon.is_bucket_public),
            })
            edges.append({"source": svc_node_id, "target": db_id, "relation": "READS_WRITES"})

        # Toxic Combination: Public Ingress + (High/Crit Finding or WAF Off) + Sensitive Unencrypted Addon
        if exposed and (crit_or_high or waf_off or any(a.is_bucket_public for a in addons)):
            factors = ["Internet-facing route"]
            if waf_off:
                factors.append("Coraza WAF disabled")
            if crit_or_high:
                factors.append(f"{len(crit_or_high)} Critical/High finding(s)")
            if addons and not encrypted:
                factors.append("Sensitive datastore without grid-cryptd encryption")
            if any(a.is_bucket_public for a in addons):
                factors.append("Public object bucket")

            combo_score = min(
                100,
                max_risk + (10 if waf_off else 0) + (15 if (addons and not encrypted) else 0),
            )
            if len(factors) >= 2:
                toxic_combinations.append({
                    "service_id": str(svc.id),
                    "service_name": svc.name,
                    "risk_score": combo_score,
                    "path": f"Internet -> {svc.name}" + (f" -> {addons[0].addon_type}:{addons[0].name}" if addons else ""),
                    "factors": factors,
                    "recommended_action": (
                        "Enable grid-cryptd encryption-at-rest, patch Critical CVEs/Secrets, or quarantine ingress."
                    ),
                })

    toxic_combinations.sort(key=lambda x: x["risk_score"], reverse=True)
    return {
        "nodes": nodes,
        "edges": edges,
        "toxic_combinations": toxic_combinations[:25],
    }
