"""XDR Engine: ITDR/UEBA, Threat Intel IoC Matching, Post-Deploy DAST & SOAR Containment."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import subprocess
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.db.models import Count
from django.utils import timezone

from .models import ForensicSnapshot, NormalizedSecurityEvent, SecurityFinding
from .ocsf import record_ocsf_event
from .risk import resolve_stale_findings, upsert_finding

logger = logging.getLogger(__name__)

# Known malicious/high-risk CIDR ranges / test IoCs (documentation ranges intentionally excluded in prod
# unless matched dynamically; extensible via Threat Intel feed cache).
_DEFAULT_IOC_IPS: frozenset[str] = frozenset({
    "45.155.205.0/24",
    "185.220.100.0/22",
    "185.220.101.0/24",
    "194.26.29.0/24",
})


def run_itdr_ueba_scan(hours: int = 24) -> dict[str, Any]:
    """Detect identity anomalies (burst privilege changes, multi-IP hopping) from AuditLog."""
    from apps.core.models import AuditLog

    cutoff = timezone.now() - timedelta(hours=max(1, hours))
    active_fps: set[str] = set()
    anomalies = 0

    sensitive_logs = list(
        AuditLog.objects.filter(timestamp__gte=cutoff)
        .values("actor", "action", "target", "metadata", "timestamp")
        .order_by("-timestamp")[:500]
    )

    ips_by_actor: dict[str, set[str]] = {}
    sensitive_count_by_actor: dict[str, int] = {}
    for row in sensitive_logs:
        actor = str(row.get("actor") or "").strip()
        if not actor or actor == "system":
            continue
        action = str(row.get("action") or "").upper()
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        ip_val = str(meta.get("ip") or meta.get("client_ip") or "").strip()
        if ip_val:
            ips_by_actor.setdefault(actor, set()).add(ip_val)
        if any(k in action for k in ("API_KEY", "ENCRYPTION", "LOCKDOWN", "UNBAN", "TRANSFER", "ROLE", "DELETE")):
            sensitive_count_by_actor[actor] = sensitive_count_by_actor.get(actor, 0) + 1

    for actor, ips in ips_by_actor.items():
        if len(ips) >= 3:
            fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.IDENTITY, "multi_ip", actor)
            active_fps.add(fp)
            upsert_finding(
                fingerprint=fp,
                pillar=SecurityFinding.Pillar.XDR,
                category=SecurityFinding.Category.IDENTITY,
                severity="HIGH",
                title=f"Multi-IP session hopping detected for actor `{actor}`",
                description=f"Actor `{actor}` executed actions from {len(ips)} distinct IPs in {hours}h: {', '.join(sorted(ips)[:5])}.",
                remediation="Verify session provenance, enforce WebAuthn/2FA, and rotate active API tokens for this user.",
                privileged=True,
                emit_ocsf=True,
            )
            anomalies += 1

    for actor, cnt in sensitive_count_by_actor.items():
        if cnt >= 12:
            fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.IDENTITY, "priv_burst", actor)
            active_fps.add(fp)
            upsert_finding(
                fingerprint=fp,
                pillar=SecurityFinding.Pillar.XDR,
                category=SecurityFinding.Category.IDENTITY,
                severity="MEDIUM",
                title=f"High-frequency privileged operations by `{actor}` ({cnt} events)",
                description=f"Actor `{actor}` triggered {cnt} sensitive security/credential actions in {hours}h.",
                remediation="Review audit trail for automation loops or compromised credentials.",
                privileged=True,
            )
            anomalies += 1

    resolved = resolve_stale_findings(category=SecurityFinding.Category.IDENTITY, active_fingerprints=active_fps)
    return {"checked": True, "anomalies": anomalies, "resolved": resolved}


def match_threat_intel(extra_ips: list[str] | None = None) -> dict[str, Any]:
    """Cross-match observed actor IPs in OCSF events against known Threat Intel CIDRs."""
    nets = []
    for cidr in _DEFAULT_IOC_IPS:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            pass

    observed_ips = set(extra_ips or [])
    for ip_str in NormalizedSecurityEvent.objects.exclude(actor_ip="").values_list("actor_ip", flat=True)[:500]:
        if ip_str:
            observed_ips.add(ip_str.strip())

    matches = []
    for raw_ip in sorted(observed_ips):
        try:
            ip_obj = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        for net in nets:
            if ip_obj in net:
                matches.append({"ip": raw_ip, "matched_feed": f"Known Scanner / Tor Exit ({net})", "severity": "HIGH"})
                break
    return {
        "feeds_loaded": len(nets),
        "observed_ips_checked": len(observed_ips),
        "matches": matches,
    }


def _safe_dast_target(url: str, *, allow_private_for_test: bool = False) -> str | None:
    raw = str(url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if allow_private_for_test:
        return raw.rstrip("/")
    try:
        from apps.core.validators import validate_webhook_url
        validate_webhook_url(raw, allow_http=True)
    except Exception:
        return None
    return raw.rstrip("/")


class _SSRFSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate every HTTP 301/302/303/307/308 redirect target against SSRF guards."""

    def __init__(self, *, allow_private_for_test: bool = False) -> None:
        super().__init__()
        self._allow_private_for_test = allow_private_for_test

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urljoin

        resolved = urljoin(req.full_url, str(newurl or ""))
        if not _safe_dast_target(resolved, allow_private_for_test=self._allow_private_for_test):
            raise urllib.error.URLError(f"DAST blocked unsafe redirect target: {resolved}")
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _build_dast_opener(*, allow_private_for_test: bool = False):
    return urllib.request.build_opener(
        _SSRFSafeRedirectHandler(allow_private_for_test=allow_private_for_test)
    )


def run_post_deploy_dast(
    service,
    *,
    base_url: str | None = None,
    deployment=None,
    allow_private_for_test: bool = False,
) -> dict[str, Any]:
    """Run non-destructive SSRF-safe DAST checks against a deployed service endpoint."""
    candidate_url = base_url
    if not candidate_url:
        dom = (getattr(service, "public_domain", "") or "").strip()
        if not dom and getattr(service, "custom_domains", None):
            dom = str(service.custom_domains[0]).strip()
        if dom:
            candidate_url = f"https://{dom}" if not dom.startswith(("http://", "https://")) else dom

    target = _safe_dast_target(candidate_url or "", allow_private_for_test=allow_private_for_test)
    if not target:
        return {"skipped": True, "reason": "No SSRF-safe public endpoint resolved", "findings": 0}

    opener = _build_dast_opener(allow_private_for_test=allow_private_for_test)
    active_fps: set[str] = set()
    detected: list[dict[str, Any]] = []

    def _add_dast(rule: str, sev: str, title: str, desc: str, rem: str, path: str = "/") -> None:
        fp = SecurityFinding.build_fingerprint(SecurityFinding.Category.DAST, service.id, rule)
        active_fps.add(fp)
        upsert_finding(
            fingerprint=fp,
            pillar=SecurityFinding.Pillar.XDR,
            category=SecurityFinding.Category.DAST,
            severity=sev,
            title=title,
            description=desc,
            remediation=rem,
            service=service,
            deployment=deployment,
            file_path=path,
            cwe_id="CWE-16" if sev in ("LOW", "MEDIUM") else "CWE-200",
            internet_exposed=True,
            emit_ocsf=sev in ("CRITICAL", "HIGH"),
        )
        detected.append({"rule": rule, "severity": sev, "title": title})

    # 1. Inspect security response headers on root URL
    try:
        req = urllib.request.Request(
            f"{target}/",
            headers={"User-Agent": "SMSLY-Aegis-DAST/1.0", "Origin": "https://evil.example.com"},
            method="GET",
        )
        with opener.open(req, timeout=6) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            if hdrs.get("access-control-allow-origin") == "*" and hdrs.get("access-control-allow-credentials", "").lower() == "true":
                _add_dast(
                    "DAST_CORS_WILDCARD_CREDS", "HIGH",
                    f"Insecure CORS wildcard with credentials on `{service.name}`",
                    f"{target}/ returned Access-Control-Allow-Origin: * alongside Allow-Credentials: true.",
                    "Restrict Access-Control-Allow-Origin to explicit trusted origins.",
                )
            missing_hdrs = [
                h for h in ("x-content-type-options", "x-frame-options", "content-security-policy")
                if h not in hdrs
            ]
            if len(missing_hdrs) >= 2:
                _add_dast(
                    "DAST_MISSING_SEC_HEADERS", "LOW",
                    f"Missing browser security headers on `{service.name}`",
                    f"{target}/ lacks security headers: {', '.join(missing_hdrs)}.",
                    "Configure X-Content-Type-Options, X-Frame-Options, and Content-Security-Policy at Caddy or app middleware.",
                )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.debug("DAST root header probe unreachable for %s: %s", target, exc)

    # 2. Check for accidentally exposed dotfiles (/.env, /.git/HEAD)
    for probe_path, marker, rule, label in (
        ("/.env", "=", "DAST_EXPOSED_ENV", "Exposed .env environment file"),
        ("/.git/HEAD", "ref:", "DAST_EXPOSED_GIT", "Exposed .git/HEAD repository metadata"),
    ):
        try:
            req = urllib.request.Request(f"{target}{probe_path}", headers={"User-Agent": "SMSLY-Aegis-DAST/1.0"})
            with opener.open(req, timeout=4) as resp:
                if resp.status == 200:
                    body = resp.read(2048).decode("utf-8", errors="ignore")
                    if marker in body:
                        _add_dast(
                            rule, "CRITICAL",
                            f"{label} over HTTP on `{service.name}`",
                            f"Public request to {target}{probe_path} returned HTTP 200 with secret/repo indicators.",
                            "Block dotfile routes in Caddy/application static router and rotate any exposed secrets.",
                            path=probe_path,
                        )
        except Exception:
            pass

    resolved = resolve_stale_findings(
        category=SecurityFinding.Category.DAST, active_fingerprints=active_fps, service=service
    )
    return {"skipped": False, "target": target, "findings": len(detected), "resolved": resolved, "items": detected}


def capture_forensics_snapshot(service, *, triggered_by: str = "system", trigger: str = "manual") -> ForensicSnapshot:
    """Capture a SHA-256-signed DFIR evidence bundle for a running or crashed container."""
    container_id = str(getattr(service, "active_runtime_id", "") or getattr(service, "name", "") or "")
    processes: list[Any] = []
    diff_entries: list[Any] = []
    recent_logs = ""
    container_status = "unknown"

    try:
        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()
        if client is not None:
            ctr = client.containers.get(container_id or service.name)
            container_id = str(getattr(ctr, "id", "") or container_id)
            container_status = str(getattr(ctr, "status", "unknown") or "unknown")
            try:
                top_res = ctr.top() or {}
                raw_procs = top_res.get("Processes") if isinstance(top_res, dict) else []
                processes = list(raw_procs) if isinstance(raw_procs, (list, tuple)) else []
            except Exception:
                pass
            try:
                raw_diff = ctr.diff() or []
                diff_entries = list(raw_diff)[:100] if isinstance(raw_diff, (list, tuple)) else []
            except Exception:
                pass
            try:
                raw_logs = ctr.logs(tail=100) or b""
                if isinstance(raw_logs, (bytes, bytearray)):
                    recent_logs = raw_logs.decode("utf-8", errors="replace")
                elif isinstance(raw_logs, str):
                    recent_logs = raw_logs
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Docker forensic inspect degraded for %s: %s", service.name, exc)

    env_keys = list(service.env_vars.values_list("key", flat=True)) if hasattr(service, "env_vars") else []
    raw_bundle = {
        "captured_at": timezone.now().isoformat(),
        "service_id": str(service.id),
        "service_name": service.name,
        "container_id": str(container_id),
        "container_status": str(container_status),
        "processes": processes[:100],
        "filesystem_diff": diff_entries,
        "env_key_names_only": env_keys,
        "recent_logs_tail": recent_logs[-4000:],
    }
    serialized_bundle = json.dumps(raw_bundle, sort_keys=True, default=str)
    bundle = json.loads(serialized_bundle)
    digest = hashlib.sha256(serialized_bundle.encode("utf-8")).hexdigest()

    snap = ForensicSnapshot.objects.create(
        service=service,
        deployment=service.deployments.order_by("-created_at").first() if hasattr(service, "deployments") else None,
        container_id=str(container_id)[:128],
        trigger=str(trigger)[:64],
        captured_by=str(triggered_by)[:150],
        sha256_digest=digest,
        bundle=bundle,
    )
    record_ocsf_event(
        source="forensics",
        source_uid=f"dfir-{snap.id}",
        title=f"Forensic DFIR Snapshot Captured ({service.name})",
        details=f"Trigger={trigger} SHA256={digest[:16]}...",
        severity="INFO",
        target=service.name,
        actor_user=triggered_by,
        service=service,
    )
    return snap


def _container_matches_service(ctr, service) -> bool:
    """Match a Docker container strictly to *service* without prefix-collision on sibling services."""
    labels = getattr(ctr, "labels", None) or {}
    if isinstance(labels, dict):
        lbl_svc_id = str(labels.get("smsly.service_id") or labels.get("com.smsly.service_id") or "").strip()
        if lbl_svc_id:
            return lbl_svc_id == str(service.id)
        lbl_svc_name = str(labels.get("smsly.service") or labels.get("com.smsly.service") or "").strip()
        if lbl_svc_name:
            return lbl_svc_name == service.name
    active_id = str(getattr(service, "active_runtime_id", "") or "").strip()
    ctr_id = str(getattr(ctr, "id", "") or "").strip()
    if active_id and ctr_id and (ctr_id == active_id or ctr_id.startswith(active_id)):
        return True
    ctr_name = str(getattr(ctr, "name", "") or "").strip()
    return ctr_name == service.name


def _sync_reverse_proxy_routes() -> None:
    try:
        from apps.deployments.services.proxy import _regenerate_caddyfile

        _regenerate_caddyfile()
    except Exception as exc:
        logger.debug("Reverse proxy route sync skipped during SOAR: %s", exc)


def execute_soar_action(
    action: str,
    user,
    *,
    service=None,
    ip: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Execute granular SOAR containment / diagnostic playbooks with immutable AuditLog."""
    from apps.core.models import AuditLog

    actor = user.get_username() if user and user.is_authenticated else "system"
    act = str(action or "").strip().lower()
    note = (reason or f"SOAR {act} executed by {actor}").strip()
    result: dict[str, Any] = {"ok": False, "action": act}

    if act == "lock_service_cryptd":
        if service is None:
            raise ValueError("service is required for lock_service_cryptd")
        from apps.core.crypto_daemon import CryptdError, cryptd_client
        if not cryptd_client.is_enabled():
            return {"ok": False, "action": act, "error": "grid-cryptd daemon is not enabled on this platform."}
        try:
            res = cryptd_client.lock_service(str(service.id), reason=note)
            result = {"ok": True, "action": act, "cryptd": res}
        except CryptdError as exc:
            return {"ok": False, "action": act, "error": str(exc)}

    elif act == "quarantine_service":
        if service is None:
            raise ValueError("service is required for quarantine_service")
        snap = capture_forensics_snapshot(service, triggered_by=actor, trigger="pre_quarantine")
        stopped_containers = 0
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            if client is not None:
                for ctr in client.containers.list():
                    if _container_matches_service(ctr, service):
                        ctr.pause()
                        stopped_containers += 1
        except Exception as exc:
            logger.debug("Container pause during quarantine failed/skipped: %s", exc)
        # Also revoke public traffic visibility immediately and sync proxy
        service.is_public = False
        service.save(update_fields=["is_public"])
        _sync_reverse_proxy_routes()
        result = {
            "ok": True,
            "action": act,
            "forensic_snapshot_id": str(snap.id),
            "containers_paused": stopped_containers,
            "is_public": False,
        }

    elif act == "unquarantine_service":
        if service is None:
            raise ValueError("service is required for unquarantine_service")
        resumed_containers = 0
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            if client is not None:
                for ctr in client.containers.list(all=True):
                    if _container_matches_service(ctr, service):
                        try:
                            ctr.unpause()
                            resumed_containers += 1
                        except Exception:
                            pass
        except Exception as exc:
            logger.debug("Container unpause during unquarantine failed/skipped: %s", exc)
        service.is_public = True
        service.save(update_fields=["is_public"])
        _sync_reverse_proxy_routes()
        result = {
            "ok": True,
            "action": act,
            "containers_resumed": resumed_containers,
            "is_public": True,
        }

    elif act == "capture_forensics":
        if service is None:
            raise ValueError("service is required for capture_forensics")
        snap = capture_forensics_snapshot(service, triggered_by=actor, trigger="operator_request")
        result = {
            "ok": True,
            "action": act,
            "snapshot_id": str(snap.id),
            "sha256_digest": snap.sha256_digest,
        }

    elif act == "ban_ip":
        try:
            ipaddress.ip_address(ip.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid IP address: {ip!r}") from exc
        from apps.crowdsec.services import get_crowdsec_service
        svc = get_crowdsec_service()
        ban_ok = svc.add_decision(ip.strip(), duration="4h", reason=note)
        result = {"ok": bool(ban_ok), "action": act, "ip": ip.strip()}

    elif act == "run_dast":
        if service is None:
            raise ValueError("service is required for run_dast")
        result = {"ok": True, "action": act, "dast": run_post_deploy_dast(service)}

    else:
        raise ValueError(f"Unknown SOAR action: {act}")

    try:
        AuditLog(
            user=user if user and user.is_authenticated else None,
            actor=actor,
            action=f"SOAR_{act.upper()}",
            target=f"Service: {service.name}" if service else (ip or "platform"),
            metadata={
                "service_id": str(service.id) if service else "",
                "ip": ip,
                "reason": note,
                "ok": result.get("ok", False),
            },
        ).save()
    except Exception:
        logger.debug("SOAR AuditLog save skipped", exc_info=True)

    return result
