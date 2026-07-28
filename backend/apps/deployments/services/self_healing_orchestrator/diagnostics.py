import logging
import shlex

from django.utils import timezone

from .base import DIAGNOSTIC_TIMEOUT
from .enums import FailureType, RecoveryAction
from .models import DiagnosticResult

logger = logging.getLogger(__name__)


class DiagnosticsMixin:

    _BUILDX_BROKEN_MARKERS = (
        "failed to recreate the buildx default builder",
        "buildx default builder",
        "no such builder: default",
        'error: failed to solve: failed to compute cache key',
    )

    def run_full_diagnostics(self, deployment=None) -> DiagnosticResult:
        """
        Run comprehensive diagnostics on the remote node.

        Collects: Docker status, container state/logs, disk usage, memory,
        network connectivity, and deployment-specific info.
        """
        self._log("Starting full diagnostics")
        result = DiagnosticResult()
        result.raw_diagnostics["timestamp"] = timezone.now().isoformat()

        try:
            result.docker_running = self._check_docker_daemon()
            result.raw_diagnostics["docker_running"] = result.docker_running

            if not result.docker_running:
                result.failure_type = FailureType.DOCKER_DAEMON_DOWN
                result.error_details = "Docker daemon is not running"
                result.suggested_actions.append(RecoveryAction.RESTART_DOCKER_DAEMON)
                result.suggested_actions.append(RecoveryAction.RESTART_STACK)
                self._log("Docker daemon is down")
                self._diagnostics = result
                return result

            self._check_resource_usage(result)

            if deployment:
                self._diagnose_deployment(deployment, result)
            else:
                self._diagnose_general(result)

            self._check_network(result)

        except Exception as exc:
            result.success = False
            result.failure_type = FailureType.NETWORK_UNREACHABLE
            result.error_details = f"Diagnostics failed: {exc}"
            self._log(f"Diagnostics exception: {exc}")

        self._diagnostics = result
        self._log(f"Diagnostics complete: {result.failure_type.value}")
        return result

    def _check_docker_daemon(self) -> bool:
        """Check if Docker daemon is running on the remote node."""
        _out, _err, code = self._exec("docker info --format '{{.ServerVersion}}'", timeout=10)
        return code == 0

    def _check_resource_usage(self, result: DiagnosticResult):
        """Check disk and memory usage on the remote node."""
        try:
            out, _, code = self._exec(
                "df -h / | awk 'NR==2 {gsub(/%/,\"\"); print $5}'",
                timeout=10,
            )
            if code == 0 and out.strip():
                result.disk_usage_pct = float(out.strip())
                if result.disk_usage_pct > 90:
                    result.failure_type = FailureType.DISK_FULL
                    result.suggested_actions.append(RecoveryAction.PRUNE_IMAGES)
                    result.suggested_actions.append(RecoveryAction.PRUNE_VOLUMES)
                    self._log(f"Disk usage critical: {result.disk_usage_pct}%")
        except Exception as exc:
            logger.debug("Failed to check disk usage during diagnostics: %s", exc)

        try:
            out, _, code = self._exec(
                "free | awk '/Mem:/ {printf \"%.1f\", $3/$2 * 100}'",
                timeout=10,
            )
            if code == 0 and out.strip():
                result.memory_usage_pct = float(out.strip())
                if result.memory_usage_pct > 90:
                    if result.failure_type == FailureType.UNKNOWN:
                        result.failure_type = FailureType.OUT_OF_MEMORY
                    result.suggested_actions.append(RecoveryAction.INCREASE_RESOURCES)
                    self._log(f"Memory usage high: {result.memory_usage_pct}%")
        except Exception as exc:
            logger.debug("Memory usage check failed: %s", exc)

    def _diagnose_deployment(self, deployment, result: DiagnosticResult):
        """Diagnose issues specific to a deployment."""
        service_name = getattr(deployment.service, "name", "") if hasattr(deployment, "service") else ""
        container_name = getattr(deployment, "container_id", "") or ""

        if container_name:
            self._inspect_container(container_name, result)
            self._get_container_logs(container_name, result)

            state = result.container_state.lower()
            if not state:
                # Container doesn't exist — need to rebuild/redeploy
                result.failure_type = FailureType.CONFIG_ERROR
                result.suggested_actions.append(RecoveryAction.REBUILD_CONTAINER)
                result.suggested_actions.append(RecoveryAction.RESTART_STACK)
                self._log(f"Container {container_name} not found — will rebuild")
                return

            # Batch J: detect buildx default-builder recreation
            # failure in the build logs. The build may have
            # succeeded on the master but the image pull on
            # the node failed, or the build happened on the
            # node itself and the buildx default builder
            # was corrupt. Either way the recovery is the
            # same: ensure a docker-container fallback builder
            # exists on the node.
            buildx_broken = self._looks_like_buildx_failure(
                result.container_logs
            )
            if buildx_broken:
                result.failure_type = FailureType.BUILDX_BROKEN
                result.error_details = (
                    "Docker buildx default builder recreation failed; "
                    "fallback builder will be created."
                )
                result.suggested_actions.append(RecoveryAction.REPAIR_BUILDX)
                self._log(
                    "Detected broken buildx default builder in container "
                    "logs; queuing REPAIR_BUILDX action."
                )

            if state in ("exited", "dead"):
                result.failure_type = FailureType.CONTAINER_CRASHED
                result.suggested_actions.append(RecoveryAction.RESTART_CONTAINER)
                self._log(f"Container {container_name} is {state}")

                classified = self._classify_crash(result.container_logs)
                if classified:
                    result.failure_type = classified
                    self._log(f"Crash classified as: {classified.value}")

            elif "restart" in state:
                result.failure_type = FailureType.CONTAINER_RESTARTING
                result.suggested_actions.append(RecoveryAction.RESTART_CONTAINER)
                self._log(f"Container {container_name} is in restart loop")

            elif state == "running":
                if result.disk_usage_pct > 90:
                    result.failure_type = FailureType.DISK_FULL
                elif result.memory_usage_pct > 90:
                    result.failure_type = FailureType.OUT_OF_MEMORY
                else:
                    result.success = True
                    self._log(f"Container {container_name} is running normally")
        elif service_name:
            self._find_container_by_name(service_name, result)
            # If still no state after searching by name, container doesn't exist
            if not result.container_state:
                result.failure_type = FailureType.CONFIG_ERROR
                result.suggested_actions.append(RecoveryAction.REBUILD_CONTAINER)
                result.suggested_actions.append(RecoveryAction.RESTART_STACK)
                self._log(f"No container found for {service_name} — will rebuild")

    def _diagnose_general(self, result: DiagnosticResult):
        """General diagnostics when no specific deployment is given."""
        try:
            out, _, code = self._exec(
                "docker ps --filter 'status=exited' --format '{{.Names}}: {{.Status}}' | head -10",
                timeout=15,
            )
            if code == 0 and out.strip():
                result.raw_diagnostics["exited_containers"] = out.strip()
                self._log(f"Exited containers found: {out.strip()[:200]}")
        except Exception as exc:
            logger.debug("Failed to list exited containers during diagnostics: %s", exc)

    def _inspect_container(self, container_name: str, result: DiagnosticResult):
        """Get detailed container inspection info."""
        out, _, code = self._exec(
            f"docker inspect {shlex.quote(container_name)} "
            f"--format '{{{{.State.Status}}}}|{{{{.State.ExitCode}}}}|{{{{.State.Error}}}}|{{{{.RestartCount}}}}'",
            timeout=10,
        )
        if code == 0 and out.strip():
            parts = out.strip().split("|", 3)
            result.container_state = parts[0] if len(parts) > 0 else ""
            result.raw_diagnostics["exit_code"] = parts[1] if len(parts) > 1 else ""
            result.raw_diagnostics["state_error"] = parts[2] if len(parts) > 2 else ""
            result.raw_diagnostics["restart_count"] = parts[3] if len(parts) > 3 else ""

    def _get_container_logs(self, container_name: str, result: DiagnosticResult, tail: int = 200):
        """Get recent container logs."""
        out, _, code = self._exec(
            f"docker logs --tail {tail} {shlex.quote(container_name)} 2>&1",
            timeout=DIAGNOSTIC_TIMEOUT,
        )
        if code == 0:
            result.container_logs = out
            result.raw_diagnostics["log_length"] = len(out)
        else:
            result.container_logs = f"Failed to get logs: {out}"

    def _find_container_by_name(self, service_name: str, result: DiagnosticResult):
        """Find a container by service name prefix."""
        out, _, code = self._exec(
            f"docker ps -a --filter 'name={shlex.quote(service_name)}' "
            f"--format '{{{{.ID}}}}|{{{{.Names}}}}|{{{{.Status}}}}' | head -5",
            timeout=10,
        )
        if code == 0 and out.strip():
            result.raw_diagnostics["matching_containers"] = out.strip()
            first = out.strip().split("\n")[0]
            parts = first.split("|", 2)
            if len(parts) >= 3:
                container_id = parts[0]
                result.container_status = parts[2]
                self._inspect_container(container_id, result)
                self._get_container_logs(container_id, result)

    def _check_network(self, result: DiagnosticResult):
        """Check network connectivity from the remote node."""
        try:
            _out, _, code = self._exec("ping -c 1 -W 3 8.8.8.8 2>&1", timeout=10)
            result.network_reachable = code == 0
            result.raw_diagnostics["network_reachable"] = result.network_reachable
        except Exception:
            result.network_reachable = False

    def _classify_crash(self, logs: str) -> FailureType | None:
        """Classify the type of crash from container logs."""
        if not logs:
            return None

        log_lower = logs.lower()

        if any(p in log_lower for p in ["oom", "out of memory", "killed", "signal 9"]):
            return FailureType.OUT_OF_MEMORY

        if any(p in log_lower for p in ["eaddrinuse", "address already in use", "bind: address already in use"]):
            return FailureType.PORT_CONFLICT

        if any(p in log_lower for p in ["pull access denied", "not found: manifest", "manifest unknown"]):
            return FailureType.IMAGE_PULL_FAILED

        if any(p in log_lower for p in ["permission denied", "eacces", "eperm"]):
            return FailureType.CONFIG_ERROR

        if any(p in log_lower for p in ["network unreachable", "connection refused", "no route to host"]):
            return FailureType.NETWORK_UNREACHABLE

        if any(p in log_lower for p in ["no space left on device", "disk full", "enospc"]):
            return FailureType.DISK_FULL

        return FailureType.CONTAINER_CRASHED

    def _looks_like_buildx_failure(self, text: str) -> bool:
        """Return True if ``text`` looks like a buildx default-
        builder recreation failure (the most common cause of a
        successful-on-master / failing-on-node image pull or
        local node build).
        """
        if not text:
            return False
        needle = text.lower()
        return any(marker in needle for marker in self._BUILDX_BROKEN_MARKERS)
