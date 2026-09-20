"""security views."""
import logging
import subprocess

logger = logging.getLogger(__name__)



from rest_framework import permissions
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
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
    permission_classes = [permissions.IsAdminUser]

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

        # ── seccomp ─────────────────────────────────────────────────
        seccomp = {"enabled": False}
        try:
            seccomp_result = subprocess.run(
                ["docker", "info", "--format", "{{json .SecurityOptions}}"],
                capture_output=True, text=True, timeout=10,
            )
            seccomp["enabled"] = "seccomp" in (seccomp_result.stdout or "")
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
