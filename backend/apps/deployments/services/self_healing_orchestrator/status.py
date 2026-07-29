import shlex
import time

from .base import DIAGNOSTIC_TIMEOUT, RECOVERY_TIMEOUT


class StatusMixin:

    def get_logs(self, container_name: str = "", tail: int = 100) -> str:
        """Get container logs from the remote node."""
        if not container_name:
            container_name = "backend"
        out, _, code = self._exec(
            f"docker logs --tail {tail} {shlex.quote(container_name)} 2>&1",
            timeout=DIAGNOSTIC_TIMEOUT,
        )
        self._close_ssh()
        return out if code == 0 else f"Error: {out}"

    def get_container_status(self, container_name: str = "") -> str:
        """Get status of all or specific containers."""
        if container_name:
            cmd = f"docker inspect {shlex.quote(container_name)} --format '{{{{.State.Status}}}}|{{{{.State.ExitCode}}}}|{{{{.RestartCount}}}}'"
        else:
            cmd = "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.State}}'"
        out, _, code = self._exec(cmd, timeout=DIAGNOSTIC_TIMEOUT)
        self._close_ssh()
        return out if code == 0 else f"Error: {out}"

    def restart_service(self, service_name: str) -> dict:
        """Restart a specific service on the remote node."""
        self._log(f"Manual restart requested for: {service_name}")
        cmd = self._compose_cmd(f"restart {shlex.quote(service_name)}")
        out, err, code = self._exec(cmd, timeout=RECOVERY_TIMEOUT)

        time.sleep(10)

        status = self._verify_container_running(service_name)
        self._close_ssh()

        return {
            "success": code == 0,
            "output": (out + err)[:1000],
            "current_status": status,
        }

    def get_node_health(self) -> dict:
        """Get overall node health summary."""
        diagnostics = self.run_full_diagnostics()
        self._close_ssh()

        return {
            "docker_running": diagnostics.docker_running,
            "disk_usage_pct": diagnostics.disk_usage_pct,
            "memory_usage_pct": diagnostics.memory_usage_pct,
            "network_reachable": diagnostics.network_reachable,
            "failure_type": diagnostics.failure_type.value,
            "exited_containers": diagnostics.raw_diagnostics.get("exited_containers", ""),
        }

    def get_heal_log(self) -> list[str]:
        """Return the heal log for this session."""
        return list(self._heal_log)
