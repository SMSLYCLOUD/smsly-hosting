"""security views."""
import logging

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
        falco = {"running": False, "container": "smsly-falco", "driver": "unknown", "events_detected": 0}
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
                        crowdsec["active_bans"] = len(bans) if isinstance(bans, list) else 0
                    else:
                        crowdsec["active_bans"] = -1
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
                    crowdsec["active_bans"] = -1
            else:
                crowdsec["active_bans"] = 0

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
        except Exception:
            pass

        return Response({
            "container_runtime": container_runtime,
            "apparmor": apparmor,
            "seccomp": seccomp,
            "no_new_privileges": no_new_privs,
            "falco": falco,
            "crowdsec": crowdsec,
            "ufw": ufw,
            "fail2ban": fail2ban,
            "auditd": auditd,
            "docker_socket_proxy": socket_proxy,
            "trivy": trivy,
            "device_trust": device_trust,
            "kernel_hardening": kernel,
        })
