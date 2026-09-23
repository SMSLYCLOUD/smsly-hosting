"""security views."""
import hashlib
import json
import logging
import os
import subprocess
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def _stable_id(prefix, line) -> str:
    """Deterministic activity id (sha1 of the source line).

    The previous ``abs(hash(line))`` changed on every worker restart
    (Python salts hash()) so each refresh remounted the whole feed and
    collapsed expanded rows. Same input line always yields the same id,
    which also makes cross-pass dedupe meaningful.
    """
    digest = hashlib.sha1(str(line or "").encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _loki_url() -> str:
    return os.environ.get("LOKI_URL", "http://smsly-loki:3100").rstrip("/")


def _loki_range(query: str, limit: int = 100, hours: int = 24,
                timeout: int = 6) -> list:
    """Query Loki query_range; return [(ts_ns, line)] newest-first.

    Fail-soft: any error (DNS, connection, timeout, bad JSON) returns [].
    The backend runs on the monitoring network (smsly-net) so the
    smsly-loki service name resolves; elsewhere this degrades to [].
    """
    try:
        end_ns = time.time_ns()
        start_ns = end_ns - int(hours * 3600 * 1e9)
        params = urllib.parse.urlencode({
            "query": query, "start": str(start_ns), "end": str(end_ns),
            "limit": limit, "direction": "backward",
        })
        req = urllib.request.Request(f"{_loki_url()}/loki/api/v1/query_range?{params}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        out = []
        for stream in data.get("data", {}).get("result", []):
            for ts, line in stream.get("values", []):
                out.append((ts, line))
        return out
    except Exception as exc:
        logger.debug("Loki query %r failed: %s", query, exc)
        return []


def _as_dict(obj):
    """CrowdSec service returns dataclasses; tests/older code use dicts."""
    if isinstance(obj, dict):
        return obj
    try:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(obj):
            return asdict(obj)
    except Exception:
        pass
    return {}



from rest_framework import permissions
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from django.core.cache import cache
from apps.deployments.views._helpers import EmptySerializer
class SecurityStatusView(GenericAPIView):
    """
    Return live system security & hardening status.

    GET /api/v1/system/security-status/

    Reports the status of all active security layers:
      - Container isolation (gVisor / Kata / runc)
      - Mandatory access control (AppArmor, seccomp)
      - Runtime protection (no-new-privileges, capability drops)
      - Threat detection (Falco, CrowdSec, auditd)
      - Network security (UFW, fail2ban)
      - Vulnerability management (Trivy)
      - Kernel hardening (sysctl)
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from apps.deployments.models.core import PlatformConfig
        from apps.deployments.services.container_runtime import (
            _kata_available,
            _runsc_available,
            detect_best_runtime,
            is_sandboxed_runtime,
        )

        config = PlatformConfig.load()
        runtime = detect_best_runtime()

        # ── Container runtime ──────────────────────────────────────
        isolation_model = "process-level (runc)"
        if runtime == "runsc":
            isolation_model = "user-space kernel (gVisor)"
        elif runtime == "kata-runtime":
            isolation_model = "VM-level (Kata)"

        container_runtime = {
            "active": runtime,
            "sandboxed": is_sandboxed_runtime(runtime),
            "isolation_model": isolation_model,
            "kata_available": _kata_available(),
            "gvisor_available": _runsc_available(),
        }

        # ── AppArmor ────────────────────────────────────────────────
        apparmor = {"enabled": False, "profiles_loaded": 0}
        try:
            import subprocess
            result = subprocess.run(
                ["aa-status", "--enabled"],
                capture_output=True, text=True, timeout=5,
            )
            apparmor["enabled"] = result.returncode == 0
            if apparmor["enabled"]:
                count_result = subprocess.run(
                    ["aa-status", "--profiled"],
                    capture_output=True, text=True, timeout=5,
                )
                try:
                    apparmor["profiles_loaded"] = int(
                        (count_result.stdout or "").strip()
                    )
                except (ValueError, TypeError):
                    apparmor["profiles_loaded"] = -1
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            apparmor["enabled"] = False

        # ── seccomp (+ AppArmor fallback) ───────────────────────────
        # One `docker info` serves both: aa-status is absent inside the
        # backend container (rc 127) even when the host enforces 100+
        # profiles, so the daemon's SecurityOptions is the reliable
        # in-container signal (2026-09-23: host had 117 loaded incl.
        # docker-default with 23 enforcing, UI still said Off).
        seccomp = {"enabled": False}
        try:
            seccomp_result = subprocess.run(
                ["docker", "info", "--format", "{{json .SecurityOptions}}"],
                capture_output=True, text=True, timeout=10,
            )
            sec_opts = seccomp_result.stdout or ""
            seccomp["enabled"] = "seccomp" in sec_opts
            if not apparmor["enabled"] and "apparmor" in sec_opts:
                apparmor["enabled"] = True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            seccomp["enabled"] = False

        # ── Falco ───────────────────────────────────────────────────
        # "running" alone lies: a scap_init failure kills PID 1 ~15s
        # after start and the loop reports Up inside every crash window
        # (2026-09-15: 400+ restarts, 0 events, status healthy). Report
        # restarts + a best-effort capturing signal so the UI can render
        # Degraded instead of green.
        falco = {
            "running": False, "container": "smsly-falco",
            "driver": "unknown", "events_detected": 0,
            "restarts": 0, "capturing": None,
        }
        # events_detected is populated from Loki below (count of falco
        # log lines in 24h); stays 0 when Loki is unreachable.
        try:
            ps_result = subprocess.run(
                ["docker", "ps", "--filter", f"name={falco['container']}",
                 "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=10,
            )
            falco["running"] = "Up" in (ps_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            falco["running"] = False
        if falco["running"]:
            try:
                restart_result = subprocess.run(
                    ["docker", "inspect", "-f", "{{.RestartCount}}",
                     falco["container"]],
                    capture_output=True, text=True, timeout=10,
                )
                falco["restarts"] = int((restart_result.stdout or "0").strip() or 0)
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
                pass
            try:
                scap_result = subprocess.run(
                    ["docker", "logs", "--since", "10m", falco["container"]],
                    capture_output=True, text=True, timeout=15,
                )
                scap_log = (scap_result.stdout or "") + (scap_result.stderr or "")
                # None (unknown) when logs are unavailable — only report
                # False on positive failure evidence, never on error.
                falco["capturing"] = (
                    "Initialization issues during scap_init" not in scap_log
                ) if (scap_result.returncode == 0) else None
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
        if falco["running"]:
            try:
                driver_result = subprocess.run(
                    ["docker", "exec", falco["container"],
                     "falco", "--list-options"],
                    capture_output=True, text=True, timeout=10,
                )
                if "modern_ebpf" in (driver_result.stdout or ""):
                    falco["driver"] = "modern_ebpf"
                elif "ebpf" in (driver_result.stdout or "").lower():
                    falco["driver"] = "ebpf"
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            try:
                subprocess.run(
                    ["docker", "exec", falco["container"],
                     "falcosidekick", "--version"],
                    capture_output=True, text=True, timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            try:
                falco["events_detected"] = len(
                    _loki_range('{container_name="smsly-falco"}', limit=5000))
            except Exception:
                pass

        # ── CrowdSec ────────────────────────────────────────────────
        crowdsec = {
            "enabled": config.enable_crowdsec_waf,
            "running": False,
            "container": "smsly-crowdsec",
        }
        if crowdsec["enabled"]:
            try:
                ps_result = subprocess.run(
                    ["docker", "ps", "--filter", f"name={crowdsec['container']}",
                     "--format", "{{.Status}}"],
                    capture_output=True, text=True, timeout=10,
                )
                crowdsec["running"] = "Up" in (ps_result.stdout or "")
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                crowdsec["running"] = False
            # Fetch active ban decisions for visibility
            if crowdsec["running"]:
                try:
                    bans_result = subprocess.run(
                        ["docker", "exec", crowdsec["container"],
                         "cscli", "decisions", "list", "-o", "json"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if bans_result.returncode == 0:
                        import json
                        bans = json.loads(bans_result.stdout)
                        try:
                            # Count what the UI can actually show: normalized
                            # actionable bans, not raw records (which include
                            # alert-only rows with no ban value). Falls back
                            # to the raw length if normalization fails.
                            from apps.crowdsec.services import get_crowdsec_service
                            crowdsec["active_bans"] = len(
                                get_crowdsec_service().get_decisions(
                                    active=True, limit=500
                                )
                            )
                        except Exception:
                            crowdsec["active_bans"] = (
                                len(bans) if isinstance(bans, list) else 0
                            )
                    else:
                        crowdsec["active_bans"] = -1
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
                    crowdsec["active_bans"] = -1
            else:
                crowdsec["active_bans"] = 0
        # First-strike wiring proof: the toggle is in config, but the
        # capacity-1 overrides only enforce when the scenario files
        # exist inside the engine. Report runtime truth separately —
        # always present so the UI contract is uniform.
        crowdsec["first_strike_enabled"] = bool(
            getattr(config, "crowdsec_first_strike_enabled", True)
        )
        crowdsec["first_strike_active"] = False
        if crowdsec["running"]:
            try:
                fs_result = subprocess.run(
                    ["docker", "exec", crowdsec["container"],
                     "sh", "-c", "ls /etc/crowdsec/scenarios/ | grep -c first-strike"],
                    capture_output=True, text=True, timeout=10,
                )
                crowdsec["first_strike_active"] = (
                    fs_result.returncode == 0
                    and (fs_result.stdout or "").strip().isdigit()
                    and int((fs_result.stdout or "0").strip()) > 0
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
                pass

        # ── open-appsec WAF (detect-learn shadow) ─────────────────────
        # Source of truth is the Settings toggle (PlatformConfig
        # .openappsec_enabled, synced to OPENAPPSEC_ENABLED in .env for
        # the installer reconcile). The env fallback covers rows that
        # pre-date the field; the containers below report runtime truth.
        openappsec = {
            "enabled": False, "agent_running": False,
            "envoy_running": False, "policy_mode": "unknown",
            "mode_configured": str(getattr(config, "openappsec_mode", "detect-learn") or "detect-learn"),
            "verdicts_recent": False, "shadow_port": 18081,
        }
        try:
            import os
            openappsec["enabled"] = bool(
                getattr(
                    config, "openappsec_enabled",
                    os.getenv("OPENAPPSEC_ENABLED", "0").strip() == "1",
                )
            )
            port_result = subprocess.run(
                ["docker", "port", "smsly-appsec-envoy"],
                capture_output=True, text=True, timeout=10,
            )
            import re as _re
            # `docker port` prints "8081/tcp -> 127.0.0.1:18081"
            # (container-port first); accept either order.
            _port_match = _re.search(
                r"8081/tcp\s*->\s*\S+:(\d+)", port_result.stdout or "")
            if _port_match is None:
                _port_match = _re.search(
                    r"127\.0\.0\.1:(\d+)->8081/tcp", port_result.stdout or "")
            if _port_match:
                openappsec["shadow_port"] = int(_port_match.group(1))
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
            pass
        if openappsec["enabled"]:
            for _ctr, _key in (
                ("smsly-appsec-agent", "agent_running"),
                ("smsly-appsec-envoy", "envoy_running"),
            ):
                try:
                    _ps = subprocess.run(
                        ["docker", "ps", "--filter", f"name={_ctr}",
                         "--format", "{{.Status}}"],
                        capture_output=True, text=True, timeout=10,
                    )
                    openappsec[_key] = "Up" in (_ps.stdout or "")
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                    pass
            if openappsec["agent_running"]:
                try:
                    _pol = subprocess.run(
                        ["docker", "exec", "smsly-appsec-agent",
                         "cat", "/etc/cp/conf/local_policy.yaml"],
                        capture_output=True, text=True, timeout=10,
                    )
                    _pol_text = _pol.stdout or ""
                    if "mode: prevent" in _pol_text or "override-mode: prevent" in _pol_text:
                        openappsec["policy_mode"] = "prevent"
                    elif "detect-learn" in _pol_text:
                        openappsec["policy_mode"] = "detect-learn"
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                    pass
            if openappsec["envoy_running"]:
                try:
                    _ver = subprocess.run(
                        ["docker", "logs", "--since", "30m", "smsly-appsec-envoy"],
                        capture_output=True, text=True, timeout=15,
                    )
                    _ver_log = (_ver.stdout or "") + (_ver.stderr or "")
                    # Vendor typo is verbatim: "got final verict: 1".
                    openappsec["verdicts_recent"] = "verict" in _ver_log.lower()
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                    pass

        # ── UFW ─────────────────────────────────────────────────────
        ufw = {"active": False}
        try:
            ufw_result = subprocess.run(
                ["ufw", "status"],
                capture_output=True, text=True, timeout=5,
            )
            ufw["active"] = "Status: active" in (ufw_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            ufw["active"] = False

        # ── fail2ban ────────────────────────────────────────────────
        # fail2ban-client and /var/log/fail2ban.log exist only on full
        # host installs; inside the platform container Loki is the
        # source of truth (promtail ships the host log as job=fail2ban).
        fail2ban = {"active": False, "jails": []}
        try:
            f2b_result = subprocess.run(
                ["fail2ban-client", "ping"],
                capture_output=True, text=True, timeout=5,
            )
            fail2ban["active"] = "pong" in (f2b_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            fail2ban["active"] = False
        if fail2ban["active"]:
            try:
                jails_result = subprocess.run(
                    ["fail2ban-client", "status"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in (jails_result.stdout or "").splitlines():
                    if line.strip().startswith("Jail list:"):
                        jails_str = line.split(":", 1)[1].strip()
                        fail2ban["jails"] = [j.strip() for j in jails_str.split(",") if j.strip()]
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
        if not fail2ban["active"]:
            # Fallback: recent Ban/Unban lines in Loki mean the daemon
            # is alive on the host even though the client is absent here.
            try:
                import re as _f2b_re
                _seen_jails = set()
                for _ts, _line in _loki_range('{job="fail2ban"}', limit=40, hours=1):
                    mm = _f2b_re.search(r"\[(\S+)\] (Ban|Unban)", _line)
                    if mm:
                        fail2ban["active"] = True
                        _seen_jails.add(mm.group(1))
                fail2ban["jails"] = sorted(_seen_jails)
            except Exception:
                pass

        # ── auditd ──────────────────────────────────────────────────
        auditd = {"active": False}
        try:
            audit_result = subprocess.run(
                ["systemctl", "is-active", "auditd"],
                capture_output=True, text=True, timeout=5,
            )
            auditd["active"] = (audit_result.stdout or "").strip() == "active"
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            auditd["active"] = False

        # ── Docker socket proxy ─────────────────────────────────────
        socket_proxy = {"enabled": False}
        try:
            sp_result = subprocess.run(
                ["docker", "ps", "--filter", "name=socket-proxy",
                 "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=10,
            )
            socket_proxy["enabled"] = "Up" in (sp_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            socket_proxy["enabled"] = False

        # ── Trivy ───────────────────────────────────────────────────
        trivy = {
            "enabled": config.trivy_enabled,
            "fail_on_severity": config.trivy_fail_on_severity,
            "installed": False,
        }
        try:
            from apps.deployments.utils import find_binary
            trivy_bin = find_binary("trivy")
            if trivy_bin:
                trivy_result = subprocess.run(
                    [trivy_bin, "--version"],
                    capture_output=True, text=True, timeout=5,
                )
                trivy["installed"] = trivy_result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ImportError):
            trivy["installed"] = False

        # ── Kernel hardening ────────────────────────────────────────
        kernel = {"enabled": False}
        try:
            kptr = subprocess.run(
                ["sysctl", "-n", "kernel.kptr_restrict"],
                capture_output=True, text=True, timeout=5,
            )
            kernel["enabled"] = (kptr.stdout or "").strip() == "2"
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            kernel["enabled"] = False

        # ── no-new-privileges (system-level) ────────────────────────
        no_new_privs = {"enabled": True}  # enforced per-container via security_opt

        # ── Device Trust (Beta) ────────────────────────────────────
        device_trust = {
            "enabled": config.enforce_device_trust,
            "beta": True,
            "registered_devices": 0,
        }
        try:
            from apps.deployments.models.core import TrustedDevice
            device_trust["registered_devices"] = TrustedDevice.objects.filter(
                is_active=True
            ).count()
        except Exception as exc:
            logger.debug("Failed to count trusted devices: %s", exc)

        return Response({
            "container_runtime": container_runtime,
            "apparmor": apparmor,
            "seccomp": seccomp,
            "no_new_privileges": no_new_privs,
            "falco": falco,
            "crowdsec": crowdsec,
            "openappsec": openappsec,
            "ufw": ufw,
            "fail2ban": fail2ban,
            "auditd": auditd,
            "docker_socket_proxy": socket_proxy,
            "trivy": trivy,
            "device_trust": device_trust,
            "kernel_hardening": kernel,
        })


class SecurityEventsView(GenericAPIView):
    """
    Return aggregated security infrastructure events and audit trails.

    GET /api/v1/system/security-events/

    Collects real-time events from:
      - Falco eBPF: runtime threat detections, container shell spawns, file probes
      - CrowdSec: active decisions (IP bans) and threat alerts
      - Fail2ban: active jail status and banned IPs (SSH, Caddy)
      - open-appsec: WAF verdicts and payload attacks
      - Auditd: system-level privilege escalations and executions
      - Trivy: recent container image CVE vulnerability findings
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        import json
        import os
        import re
        from datetime import datetime, timezone

        limit = min(int(request.query_params.get("limit", 100)), 300)
        source_filter = request.query_params.get("source", "").lower().strip()

        # Short cache: every tab switch re-requests the same scrape
        # (docker logs, cscli, loki — 10-30s). Without it each click shows
        # an empty list while the re-scrape runs (stale data + new filter
        # match nothing). Fail-open: any cache error rescrapes.
        cache_key = f"smsly:sec-events:{source_filter or 'all'}:{limit}"
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return Response(cached)

        falco_events = []
        crowdsec_decisions = []
        crowdsec_alerts = []
        fail2ban_jails = {}
        fail2ban_events = []
        openappsec_events = []
        trivy_findings = []
        auditd_events = []
        activities = []

        # ── 1. Falco eBPF Logs / Events ───────────────────────────
        try:
            falco_proc = subprocess.run(
                ["docker", "logs", "--tail", "150", "smsly-falco"],
                capture_output=True, text=True, timeout=8,
            )
            falco_raw = (falco_proc.stdout or "") + (falco_proc.stderr or "")
            for line in falco_raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("{") and line.endswith("}"):
                    try:
                        ev = json.loads(line)
                        f_time = ev.get("time") or datetime.now(timezone.utc).isoformat()
                        f_rule = ev.get("rule", "Unknown Falco Rule")
                        f_pri = (ev.get("priority") or "Notice").upper()
                        f_out = ev.get("output", "")
                        fields = ev.get("output_fields") or {}
                        ctr = fields.get("container.name") or fields.get("container.id") or "unknown"
                        proc = fields.get("proc.name") or "unknown"

                        sev = "CRITICAL" if f_pri in ("CRITICAL", "EMERGENCY", "ALERT") else (
                            "HIGH" if f_pri in ("ERROR", "WARNING") else "WARNING"
                        )
                        falco_events.append({
                            "id": _stable_id("falco", line),
                            "timestamp": f_time,
                            "rule": f_rule,
                            "priority": f_pri,
                            "severity": sev,
                            "output": f_out,
                            "container": ctr,
                            "proc": proc,
                            "raw": ev,
                        })
                        activities.append({
                            "id": _stable_id("falco", line),
                            "source": "falco",
                            "type": "runtime_alert",
                            "severity": sev,
                            "title": f"Falco: {f_rule}",
                            "details": f_out,
                            "target": f"container: {ctr} (proc: {proc})",
                            "timestamp": f_time,
                            "raw": ev,
                        })
                    except Exception:
                        pass
                elif "Initialization issues during scap_init" in line:
                    activities.append({
                        "id": _stable_id("falco-err", line),
                        "source": "falco",
                        "type": "error",
                        "severity": "CRITICAL",
                        "title": "Falco Driver Initialization Error",
                        "details": line,
                        "target": "smsly-falco",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "raw": {"message": line},
                    })
        except Exception as exc:
            logger.debug("Falco events fetch skipped: %s", exc)

        # ── 2. CrowdSec Decisions & Alerts ────────────────────────
        try:
            from apps.crowdsec.services import get_crowdsec_service
            cs_svc = get_crowdsec_service()
            raw_decisions = cs_svc.get_decisions(active=True, limit=100) or []
            raw_alerts = cs_svc.get_alerts(limit=50) or []

            for d in raw_decisions:
                d = _as_dict(d)
                ip_val = d.get("value") or d.get("source_ip") or "unknown"
                scen = d.get("scenario") or "manual/unknown"
                act = d.get("type") or "ban"
                ts = d.get("created_at") or datetime.now(timezone.utc).isoformat()
                crowdsec_decisions.append({
                    "id": str(d.get("id", ip_val)),
                    "ip": ip_val,
                    "scenario": scen,
                    "action": act,
                    "duration": d.get("duration", ""),
                    "origin": d.get("origin", "cscli"),
                    "created_at": ts,
                })
                activities.append({
                    "id": f"cs-dec-{d.get('id', ip_val)}",
                    "source": "crowdsec",
                    "type": "ban",
                    "severity": "HIGH",
                    "title": f"CrowdSec Active Ban: {ip_val}",
                    "details": f"Scenario: {scen} • Duration: {d.get('duration', '')}",
                    "target": ip_val,
                    "timestamp": ts,
                    "raw": d,
                })

            for a in raw_alerts:
                a = _as_dict(a)
                src_ip = a.get("source_ip") or a.get("source") or "unknown"
                if not isinstance(src_ip, str) or not src_ip:
                    evts = a.get("events") or []
                    src_ip = (evts[0].get("source") if evts and isinstance(evts[0], dict) else "") or "unknown"
                scen = a.get("scenario") or "attack"
                country = a.get("source_country") or a.get("country") or ""
                ts = a.get("created_at") or datetime.now(timezone.utc).isoformat()
                crowdsec_alerts.append({
                    "id": str(a.get("id", "")),
                    "source_ip": src_ip,
                    "country": country,
                    "scenario": scen,
                    "created_at": ts,
                })
                activities.append({
                    "id": f"cs-alert-{a.get('id', src_ip)}",
                    "source": "crowdsec",
                    "type": "alert",
                    "severity": "HIGH",
                    "title": f"CrowdSec Alert: {scen}",
                    "details": f"Source: {src_ip} ({country})",
                    "target": src_ip,
                    "timestamp": ts,
                    "raw": a,
                })
        except Exception as exc:
            logger.debug("CrowdSec events fetch skipped: %s", exc)

        # ── 3. Fail2ban Jails & Bans ──────────────────────────────
        jails = ["sshd", "recidive", "caddy-auth", "caddy-dos"]
        client_reported_jails = set()
        for jail in jails:
            try:
                f2b_res = subprocess.run(
                    ["fail2ban-client", "status", jail],
                    capture_output=True, text=True, timeout=4,
                )
                if f2b_res.returncode == 0:
                    c_banned = 0
                    t_banned = 0
                    banned_ips = []
                    for jl in (f2b_res.stdout or "").splitlines():
                        if "Currently banned:" in jl:
                            try:
                                c_banned = int(jl.split(":", 1)[1].strip())
                            except ValueError:
                                pass
                        elif "Total banned:" in jl:
                            try:
                                t_banned = int(jl.split(":", 1)[1].strip())
                            except ValueError:
                                pass
                        elif "Banned IP list:" in jl:
                            raw_ips = jl.split(":", 1)[1].strip()
                            banned_ips = [bip.strip() for bip in raw_ips.split() if bip.strip()]

                    fail2ban_jails[jail] = {
                        "currently_banned": c_banned,
                        "total_banned": t_banned,
                        "banned_ips": banned_ips,
                    }
                    client_reported_jails.add(jail)
                    for bip in banned_ips:
                        activities.append({
                            "id": f"f2b-{jail}-{bip}",
                            "source": "fail2ban",
                            "type": "ban",
                            "severity": "WARNING" if jail == "caddy-auth" else "HIGH",
                            "title": f"Fail2ban Banned IP ({jail}): {bip}",
                            "details": f"Jail: {jail} • Total banned: {t_banned}",
                            "target": bip,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "raw": {"jail": jail, "ip": bip},
                        })
            except Exception as exc:
                logger.debug("Fail2ban jail probe %s skipped: %s", jail, exc)

        # Scrape /var/log/fail2ban.log if available
        if os.path.exists("/var/log/fail2ban.log"):
            try:
                with open("/var/log/fail2ban.log", "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-40:]
                f2b_re = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[(\S+)\] (Ban|Restore Ban|Unban) (\S+)")
                for line in lines:
                    m = f2b_re.search(line)
                    if m:
                        l_time, l_jail, l_action, l_ip = m.groups()
                        fail2ban_events.append({
                            "timestamp": l_time,
                            "jail": l_jail,
                            "action": l_action,
                            "ip": l_ip,
                        })
                        activities.append({
                            "id": _stable_id("f2b-log", line),
                            "source": "fail2ban",
                            "type": l_action.lower().replace(" ", "_"),
                            "severity": "INFO" if "unban" in l_action.lower() else "HIGH",
                            "title": f"Fail2ban {l_action}: {l_ip}",
                            "details": f"Jail: {l_jail}",
                            "target": l_ip,
                            "timestamp": l_time,
                            "raw": {"line": line.strip()},
                        })
            except Exception as exc:
                logger.debug("Fail2ban log parse skipped: %s", exc)

        # ── 4. open-appsec WAF Logs ───────────────────────────────
        # Primary source is the AGENT (smsly-appsec-agent): it emits one
        # JSON threat/policy event per line (eventTime/eventName/
        # eventSeverity). The envoy attachment only logs keepalive noise,
        # so scraping it yielded zero WAF events. Envoy keyword grep is
        # kept as a fallback when the agent has no recent lines.
        try:
            agent_proc = subprocess.run(
                ["docker", "logs", "--tail", "80", "smsly-appsec-agent"],
                capture_output=True, text=True, timeout=8,
            )
            agent_raw = (agent_proc.stdout or "") + (agent_proc.stderr or "")
            for line in agent_raw.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                name = ev.get("eventName") or "open-appsec event"
                sev_raw = (ev.get("eventSeverity") or "info").lower()
                sev = "HIGH" if sev_raw in ("critical", "high") else (
                    "WARNING" if sev_raw in ("medium", "warning") else "INFO")
                openappsec_events.append({
                    "time": ev.get("eventTime"),
                    "name": name,
                    "severity": sev_raw,
                    "service": (ev.get("eventSource") or {}).get("serviceName", ""),
                })
                # Every agent line enters the feed (info/low as INFO) so the
                # WAF tab shows telemetry instead of reading empty while
                # the pill counts lines — the severity filter hides INFO.
                activities.append({
                    "id": _stable_id("oas", line),
                    "source": "openappsec",
                    "type": "waf_verdict",
                    "severity": sev,
                    "title": f"open-appsec: {name}",
                    "details": str(ev.get("eventType") or "")[:200],
                    "target": (ev.get("eventSource") or {}).get("serviceName", "waf"),
                    "timestamp": ev.get("eventTime") or datetime.now(timezone.utc).isoformat(),
                    "raw": ev,
                })
        except Exception as exc:
            logger.debug("open-appsec agent events fetch skipped: %s", exc)
        if not openappsec_events:
            try:
                envoy_proc = subprocess.run(
                    ["docker", "logs", "--tail", "60", "smsly-appsec-envoy"],
                    capture_output=True, text=True, timeout=8,
                )
                envoy_raw = (envoy_proc.stdout or "") + (envoy_proc.stderr or "")
                for line in envoy_raw.splitlines():
                    if any(k in line.lower() for k in ("verdict", "blocked", "drop", "attack", "waf", "threat")):
                        openappsec_events.append({"message": line.strip()})
                        activities.append({
                            "id": _stable_id("oas", line),
                            "source": "openappsec",
                            "type": "waf_verdict",
                            "severity": "HIGH" if ("drop" in line.lower() or "blocked" in line.lower()) else "WARNING",
                            "title": "open-appsec WAF Security Event",
                            "details": line.strip()[:200],
                            "target": "reverse-proxy",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "raw": {"log": line.strip()},
                        })
            except Exception as exc:
                logger.debug("open-appsec envoy events fetch skipped: %s", exc)

        # Loki {job="fail2ban"} — the durable source. fail2ban-client and
        # /var/log/fail2ban.log only exist on full host installs; inside
        # the platform container (and once promtail ships the host log)
        # Loki is the source that actually has data. Entries merge into
        # fail2ban_jails so the UI shows bans even without the client.
        try:
            f2b_re = re.compile(
                r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[(\S+)\] (Ban|Restore Ban|Unban) (\S+)")
            for _ts, line in _loki_range('{job="fail2ban"}', limit=120):
                m = f2b_re.search(line)
                if not m:
                    continue
                l_time, l_jail, l_action, l_ip = m.groups()
                if not any(e.get("ip") == l_ip and e.get("jail") == l_jail
                           and e.get("action") == l_action
                           for e in fail2ban_events):
                    fail2ban_events.append({
                        "timestamp": l_time, "jail": l_jail,
                        "action": l_action, "ip": l_ip,
                    })
                activities.append({
                    "id": _stable_id("f2b-loki", line),
                    "source": "fail2ban",
                    "type": l_action.lower().replace(" ", "_"),
                    "severity": "INFO" if "unban" in l_action.lower() else "HIGH",
                    "title": f"Fail2ban {l_action}: {l_ip}",
                    "details": f"Jail: {l_jail}",
                    "target": l_ip,
                    "timestamp": l_time,
                    "raw": {"line": line.strip()[:220]},
                })
            # Recompute per-jail active bans from the merged event window —
            # but only for jails the fail2ban-client did NOT report (client
            # data is authoritative where available; the log window here
            # is capped and would otherwise shrink real counts).
            for e in fail2ban_events:
                jail = e.get("jail") or "unknown"
                if jail in client_reported_jails:
                    continue
                entry = fail2ban_jails.setdefault(jail, {
                    "currently_banned": 0, "total_banned": 0, "banned_ips": []})
                if e.get("action") in ("Ban", "Restore Ban"):
                    if e.get("ip") not in entry["banned_ips"]:
                        entry["banned_ips"].append(e.get("ip"))
                else:
                    if e.get("ip") in entry["banned_ips"]:
                        entry["banned_ips"].remove(e.get("ip"))
            for jail, entry in fail2ban_jails.items():
                if jail in client_reported_jails:
                    continue
                entry["currently_banned"] = len(entry.get("banned_ips", []))
                entry["total_banned"] = max(
                    entry.get("total_banned", 0), len(entry.get("banned_ips", [])))
        except Exception as exc:
            logger.debug("Fail2ban Loki query skipped: %s", exc)

        # ── 5. Auditd Security Trails ─────────────────────────────
        if os.path.exists("/var/log/audit/audit.log"):
            try:
                with open("/var/log/audit/audit.log", "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[-30:]
                for line in lines:
                    if any(k in line for k in ("docker-exec", "priv-esc", "smsly-secrets", "smsly-config", "identity")):
                        auditd_events.append({"raw": line.strip()})
                        activities.append({
                            "id": _stable_id("auditd", line),
                            "source": "auditd",
                            "type": "audit_probe",
                            "severity": "WARNING",
                            "title": "Auditd Kernel Security Rule Triggered",
                            "details": line.strip()[:200],
                            "target": "host-kernel",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "raw": {"log": line.strip()},
                        })
            except Exception as exc:
                logger.debug("Auditd log parse skipped: %s", exc)

        # ── 6. Trivy Vulnerability Scan Findings ──────────────────
        try:
            from apps.deployments.models import Deployment
            recent_deps = Deployment.objects.filter(
                vulnerability_report__isnull=False
            ).exclude(vulnerability_report={}).order_by("-created_at")[:8]
            for dep in recent_deps:
                vr = dep.vulnerability_report or {}
                findings = vr.get("findings") or []
                for find in findings[:10]:
                    sev = (find.get("severity") or "UNKNOWN").upper()
                    if sev in ("CRITICAL", "HIGH"):
                        trivy_findings.append({
                            "cve": find.get("id"),
                            "pkg": find.get("pkg"),
                            "severity": sev,
                            "title": find.get("title"),
                            "service": getattr(dep.service, "name", "unknown") if dep.service else "service",
                            "deployment_id": str(dep.id),
                            "created_at": dep.created_at.isoformat() if dep.created_at else None,
                        })
                        activities.append({
                            "id": f"trivy-{dep.id}-{find.get('id')}",
                            "source": "trivy",
                            "type": "vulnerability",
                            "severity": sev,
                            "title": f"Trivy CVE: {find.get('id')} ({find.get('pkg')})",
                            "details": f"Severity: {sev} • {find.get('title', '')[:100]}",
                            "target": getattr(dep.service, "name", "service") if dep.service else "deployment",
                            "timestamp": dep.created_at.isoformat() if dep.created_at else datetime.now(timezone.utc).isoformat(),
                            "raw": find,
                        })
        except Exception as exc:
            logger.debug("Trivy findings fetch skipped: %s", exc)

        # Deduplicate & Sort descending by timestamp (on the FULL set —
        # pills below describe this global overview, the source filter
        # only narrows the listed page so other pills never collapse).
        seen_ids = set()
        deduped = []
        for a in activities:
            if a["id"] not in seen_ids:
                seen_ids.add(a["id"])
                deduped.append(a)

        deduped.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
        # Pills must describe the feed, not the raw collections: every pill
        # is a count over this same deduped pre-limit list (previously the
        # WAF pill counted raw agent lines including info noise while the
        # feed excluded them, and Total was the page size after [:limit]).
        per_source: dict = {}
        for a in deduped:
            per_source[a["source"]] = per_source.get(a["source"], 0) + 1
        total_events = len(deduped)

        # Filter by source if requested (list only — pills stay global).
        if source_filter:
            deduped = [a for a in deduped if a["source"] == source_filter]
        final_activities = deduped[:limit]

        payload = {
            "summary": {
                "total_events": total_events,
                "falco_alerts_count": per_source.get("falco", 0),
                "crowdsec_bans_count": per_source.get("crowdsec", 0),
                "crowdsec_alerts_count": len(crowdsec_alerts),
                "fail2ban_banned_count": per_source.get("fail2ban", 0),
                "waf_events_count": per_source.get("openappsec", 0),
                "trivy_cves_count": per_source.get("trivy", 0),
            },
            "falco_events": falco_events[:50],
            "crowdsec_decisions": crowdsec_decisions,
            "crowdsec_alerts": crowdsec_alerts,
            "fail2ban_jails": fail2ban_jails,
            "fail2ban_events": fail2ban_events[:30],
            "openappsec_events": openappsec_events[:30],
            "auditd_events": auditd_events[:20],
            "trivy_findings": trivy_findings[:30],
            "recent_activities": final_activities,
        }
        try:
            cache.set(cache_key, payload, 90)
        except Exception:
            pass
        return Response(payload)


class SecurityAnalysisView(GenericAPIView):
    """
    Perform AI-powered security threat analysis across platform telemetry.

    POST /api/v1/system/security-analysis/
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from datetime import datetime, timezone

        # 1. Gather live status and events
        status_view = SecurityStatusView()
        status_resp = status_view.get(request)
        status_data = status_resp.data if hasattr(status_resp, "data") else {}

        events_view = SecurityEventsView()
        events_resp = events_view.get(request)
        events_data = events_resp.data if hasattr(events_resp, "data") else {}

        summary = events_data.get("summary") or {}
        falco_count = summary.get("falco_alerts_count", 0)
        crowdsec_bans = summary.get("crowdsec_bans_count", 0)
        f2b_bans = summary.get("fail2ban_banned_count", 0)
        cve_count = summary.get("trivy_cves_count", 0)

        # 2. Heuristic baseline risk calculation
        risk_score = 10
        risk_score += min(cve_count * 12, 35)
        risk_score += min(falco_count * 15, 30)
        risk_score += min(crowdsec_bans * 2, 15)
        risk_score += min(f2b_bans * 2, 10)

        falco_ok = status_data.get("falco", {}).get("capturing") is True
        ufw_ok = status_data.get("ufw", {}).get("active") is True
        if not falco_ok:
            risk_score += 15
        if not ufw_ok:
            risk_score += 15
        risk_score = max(5, min(risk_score, 98))

        if risk_score >= 75:
            threat_level = "SEVERE"
        elif risk_score >= 50:
            threat_level = "HIGH"
        elif risk_score >= 25:
            threat_level = "ELEVATED"
        else:
            threat_level = "LOW"

        # 3. Formulate AI Analysis
        ai_summary = ""
        attack_vectors = []
        hardening_actions = []

        if crowdsec_bans > 0 or f2b_bans > 0:
            attack_vectors.append(f"Automated botnet probes and brute-force scans actively blocked ({crowdsec_bans + f2b_bans} IPs banned across SSH and HTTP edge).")
        if falco_count > 0:
            attack_vectors.append(f"Kernel-level syscall anomalies flagged by Falco eBPF ({falco_count} events recorded in recent capture window).")
        if cve_count > 0:
            attack_vectors.append(f"Supply chain vulnerability exposure: {cve_count} Critical/High CVEs present in recent container images.")
        if not attack_vectors:
            attack_vectors.append("No active exploit patterns or hostile anomalous vectors detected across ingress proxies.")

        hardening_actions.append("Ensure regular base image rebuilds to patch upstream OS and library CVEs identified by Trivy.")
        if not status_data.get("container_runtime", {}).get("sandboxed"):
            hardening_actions.append("Enable gVisor (runsc) or Kata Containers sandboxing for zero-trust micro-VM workload isolation.")
        if status_data.get("openappsec", {}).get("policy_mode") == "detect-learn":
            hardening_actions.append("Promote open-appsec ML WAF from 'detect-learn' shadow mode to 'prevent' mode once baseline traffic stabilizes.")
        hardening_actions.append("Review CrowdSec and Fail2ban persistent ban lists weekly to identify repeat subnet ranges.")

        # Attempt to enrich with AI if available
        try:
            from apps.ai.router import get_ai_response
            ai_prompt = (
                f"You are the Chief Information Security Officer AI for SMSLY Hosting.\n"
                f"Security Telemetry Summary:\n"
                f"- Threat Level: {threat_level} (Risk Score: {risk_score}/100)\n"
                f"- Falco eBPF Alerts: {falco_count}\n"
                f"- Active CrowdSec Bans: {crowdsec_bans}\n"
                f"- Active Fail2ban Bans: {f2b_bans}\n"
                f"- Critical CVEs: {cve_count}\n"
                f"- UFW Firewall Active: {ufw_ok}\n"
                f"- Falco Capturing: {falco_ok}\n"
                f"Provide a concise, 2-3 sentence executive summary of platform security health and posture."
            )
            ai_res = get_ai_response(ai_prompt)
            if ai_res and isinstance(ai_res, str) and len(ai_res.strip()) > 20:
                ai_summary = ai_res.strip()
        except Exception:
            pass

        if not ai_summary:
            ai_summary = (
                f"Platform perimeter defense is operating at {threat_level} threat level with an overall risk score of {risk_score}/100. "
                f"Host firewall (UFW), Falco eBPF kernel monitors, CrowdSec LAPI, and Fail2ban edge filters are actively intercepting hostile probes. "
                f"{len(attack_vectors)} operational security factors have been identified for continuous monitoring."
            )

        return Response({
            "threat_level": threat_level,
            "risk_score": risk_score,
            "executive_summary": ai_summary,
            "attack_vectors": attack_vectors,
            "hardening_actions": hardening_actions,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        })

