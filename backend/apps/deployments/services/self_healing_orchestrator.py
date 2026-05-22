"""
Self-Healing Orchestrator — automated diagnosis and recovery for remote nodes.

When a deployment to a remote server/node fails, this orchestrator:
1. Runs diagnostics (logs, container status, disk, memory, network)
2. Classifies the failure type
3. Executes recovery commands (restart container, restart stack, rebuild, fix network)
4. Escalates to system intelligence (AI) when automated fixes fail
5. Verifies recovery and reports results

Designed to be called from:
- Deployment pipeline on failure
- Health monitor on service degradation
- Node watchdog on connectivity loss
- Manual API trigger
"""

import logging
import time
import re
import shlex
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field

from django.utils import timezone
from django.core.cache import cache

from .ssh_client import SSHClient
from .error_resolver import diagnose_runtime_logs

logger = logging.getLogger(__name__)

HEAL_STATE_TTL = 3600
MAX_HEAL_ATTEMPTS = 5
HEAL_COOLDOWN_SECONDS = 120
DIAGNOSTIC_TIMEOUT = 30
RECOVERY_TIMEOUT = 180
POST_RECOVERY_WAIT = 20


class FailureType(Enum):
    CONTAINER_CRASHED = "container_crashed"
    CONTAINER_RESTARTING = "container_restarting"
    OUT_OF_MEMORY = "out_of_memory"
    DISK_FULL = "disk_full"
    NETWORK_UNREACHABLE = "network_unreachable"
    DOCKER_DAEMON_DOWN = "docker_daemon_down"
    IMAGE_PULL_FAILED = "image_pull_failed"
    PORT_CONFLICT = "port_conflict"
    CONFIG_ERROR = "config_error"
    DEPLOYMENT_TIMEOUT = "deployment_timeout"
    UNKNOWN = "unknown"


class RecoveryAction(Enum):
    RESTART_CONTAINER = "restart_container"
    RESTART_STACK = "restart_stack"
    REBUILD_CONTAINER = "rebuild_container"
    PRUNE_IMAGES = "prune_images"
    PRUNE_VOLUMES = "prune_volumes"
    FIX_NETWORK = "fix_network"
    FIX_PERMISSIONS = "fix_permissions"
    INCREASE_RESOURCES = "increase_resources"
    ROLLBACK = "rollback"
    REPROVISION = "reprovision"
    ESCALATE_TO_AI = "escalate_to_ai"
    NONE = "none"


@dataclass
class DiagnosticResult:
    """Structured result from a diagnostic run."""
    success: bool = True
    failure_type: FailureType = FailureType.UNKNOWN
    container_logs: str = ""
    container_status: str = ""
    container_state: str = ""
    disk_usage_pct: float = 0.0
    memory_usage_pct: float = 0.0
    docker_running: bool = False
    network_reachable: bool = False
    error_details: str = ""
    suggested_actions: list = field(default_factory=list)
    raw_diagnostics: dict = field(default_factory=dict)


@dataclass
class RecoveryResult:
    """Structured result from a recovery attempt."""
    success: bool = False
    action_taken: RecoveryAction = RecoveryAction.NONE
    details: str = ""
    post_recovery_status: str = ""
    next_action: Optional[RecoveryAction] = None


class SelfHealingOrchestrator:
    """
    Orchestrates automated diagnosis and recovery for remote node failures.

    Usage::

        orchestrator = SelfHealingOrchestrator(server)
        result = orchestrator.heal_deployment_failure(deployment)
    """

    def __init__(self, server):
        try:
            from .models_core import ManagedServer
            fresh = ManagedServer.objects.only(
                "id", "name", "host", "ssh_key", "ssh_password",
                "ssh_user", "ssh_port", "is_lite_agent", "status",
                "wg_address",
            ).get(id=server.id if hasattr(server.id, "int") else server.pk)
            self.server = fresh
        except Exception:
            self.server = server

        self._ssh = None
        self._hosting_path = None
        self._diagnostics = DiagnosticResult()
        self._heal_log = []

    def _log(self, message: str):
        """Append to the heal log and emit a logger message."""
        self._heal_log.append(message)
        logger.info("[SelfHeal][%s] %s", self.server.name, message)

    def _get_ssh(self) -> SSHClient:
        """Get or create an SSH client for this server."""
        if self._ssh and self._ssh.client:
            return self._ssh
        self._ssh = SSHClient(
            ip=self.server.host,
            key_content=self.server.ssh_key,
            password=self.server.ssh_password,
            user=self.server.ssh_user,
            port=self.server.ssh_port,
        )
        self._ssh.connect()
        return self._ssh

    def _get_hosting_path(self) -> str:
        """Get the hosting path on the remote server."""
        if self._hosting_path:
            return self._hosting_path
        try:
            ssh = self._get_ssh()
            self._hosting_path = ssh.find_hosting_path()
        except Exception:
            self._hosting_path = "/opt/smsly-hosting"
        return self._hosting_path

    def _close_ssh(self):
        """Close the SSH connection."""
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None

    def _exec(self, command: str, timeout: int = DIAGNOSTIC_TIMEOUT, raise_on_error: bool = False):
        """Execute a command via SSH and return (out, err, code)."""
        try:
            ssh = self._get_ssh()
            return ssh.exec_command(command, timeout=timeout, raise_on_error=raise_on_error)
        except Exception as exc:
            return "", str(exc), 1

    def _compose_cmd(self, subcommand: str) -> str:
        """Build a docker compose command that works with both v1 and v2."""
        path = shlex.quote(self._get_hosting_path())
        return (
            f"cd {path} && "
            f"(docker compose {subcommand} 2>&1 "
            f"|| docker-compose {subcommand} 2>&1)"
        )

    def _can_heal(self) -> bool:
        """Check if we have credentials and the server is eligible for healing."""
        if not self.server.ssh_key and not self.server.ssh_password:
            self._log("No SSH credentials — cannot heal")
            return False
        return True

    def _check_heal_cooldown(self, scope: str) -> bool:
        """Return True if we're in cooldown for this scope."""
        key = f"selfheal:cooldown:{scope}"
        last = cache.get(key)
        if last:
            elapsed = time.time() - float(last)
            if elapsed < HEAL_COOLDOWN_SECONDS:
                self._log(f"Cooldown active ({int(HEAL_COOLDOWN_SECONDS - elapsed)}s remaining)")
                return True
        return False

    def _set_heal_cooldown(self, scope: str):
        """Set cooldown for this scope."""
        key = f"selfheal:cooldown:{scope}"
        cache.set(key, time.time(), timeout=HEAL_STATE_TTL)

    def _track_heal_attempt(self, scope: str) -> int:
        """Increment and return the heal attempt count for this scope."""
        key = f"selfheal:attempts:{scope}"
        count = int(cache.get(key, 0) or 0) + 1
        cache.set(key, count, timeout=HEAL_STATE_TTL)
        return count

    def _reset_heal_state(self, scope: str):
        """Reset heal tracking for this scope (called on success)."""
        cache.delete(f"selfheal:attempts:{scope}")
        cache.delete(f"selfheal:cooldown:{scope}")

    # ─── Diagnostics ─────────────────────────────────────────────────

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
        out, err, code = self._exec("docker info --format '{{.ServerVersion}}'", timeout=10)
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
        except Exception:
            pass

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
        except Exception:
            pass

    def _diagnose_deployment(self, deployment, result: DiagnosticResult):
        """Diagnose issues specific to a deployment."""
        service_name = getattr(deployment.service, "name", "") if hasattr(deployment, "service") else ""
        container_name = getattr(deployment, "container_id", "") or ""

        if container_name:
            self._inspect_container(container_name, result)
            self._get_container_logs(container_name, result)

            state = result.container_state.lower()
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
        except Exception:
            pass

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
            out, _, code = self._exec("ping -c 1 -W 3 8.8.8.8 2>&1", timeout=10)
            result.network_reachable = code == 0
            result.raw_diagnostics["network_reachable"] = result.network_reachable
        except Exception:
            result.network_reachable = False

    def _classify_crash(self, logs: str) -> Optional[FailureType]:
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

    # ─── Recovery Actions ────────────────────────────────────────────

    def heal_deployment_failure(self, deployment, max_attempts: int = MAX_HEAL_ATTEMPTS) -> RecoveryResult:
        """
        Main entry point: diagnose and attempt to heal a failed deployment.

        Returns a RecoveryResult with the outcome and any next actions.
        """
        scope = f"deploy:{deployment.id}"

        if not self._can_heal():
            return RecoveryResult(
                action_taken=RecoveryAction.NONE,
                details="No SSH credentials available",
            )

        if self._check_heal_cooldown(scope):
            return RecoveryResult(
                action_taken=RecoveryAction.NONE,
                details="Heal cooldown active",
            )

        attempt = self._track_heal_attempt(scope)
        if attempt > max_attempts:
            self._log(f"Max heal attempts ({max_attempts}) reached")
            return RecoveryResult(
                action_taken=RecoveryAction.ESCALATE_TO_AI,
                details=f"Exhausted {max_attempts} automated heal attempts",
                next_action=RecoveryAction.ESCALATE_TO_AI,
            )

        self._log(f"Starting heal attempt {attempt}/{max_attempts}")

        try:
            diagnostics = self.run_full_diagnostics(deployment)
            actions = diagnostics.suggested_actions

            if not actions:
                actions = [RecoveryAction.RESTART_CONTAINER]

            for action in actions:
                if attempt > max_attempts:
                    break

                self._log(f"Executing recovery action: {action.value}")
                result = self._execute_recovery(action, deployment, diagnostics)

                if result.success:
                    self._reset_heal_state(scope)
                    self._set_heal_cooldown(scope)
                    self._log(f"Recovery succeeded: {action.value}")
                    return result

                self._log(f"Recovery failed: {action.value} — {result.details}")
                attempt += 1

            if attempt > max_attempts:
                return RecoveryResult(
                    action_taken=RecoveryAction.ESCALATE_TO_AI,
                    details="All automated recovery actions failed",
                    next_action=RecoveryAction.ESCALATE_TO_AI,
                )

            return RecoveryResult(
                action_taken=RecoveryAction.NONE,
                details="No recovery action succeeded",
                next_action=RecoveryAction.ESCALATE_TO_AI,
            )

        except Exception as exc:
            self._log(f"Heal exception: {exc}")
            return RecoveryResult(
                action_taken=RecoveryAction.NONE,
                details=f"Exception during healing: {exc}",
                next_action=RecoveryAction.ESCALATE_TO_AI,
            )
        finally:
            self._close_ssh()

    def _execute_recovery(
        self,
        action: RecoveryAction,
        deployment,
        diagnostics: DiagnosticResult,
    ) -> RecoveryResult:
        """Execute a specific recovery action."""
        handlers = {
            RecoveryAction.RESTART_CONTAINER: self._restart_container,
            RecoveryAction.RESTART_STACK: self._restart_stack,
            RecoveryAction.REBUILD_CONTAINER: self._rebuild_container,
            RecoveryAction.PRUNE_IMAGES: self._prune_images,
            RecoveryAction.PRUNE_VOLUMES: self._prune_volumes,
            RecoveryAction.FIX_NETWORK: self._fix_network,
            RecoveryAction.FIX_PERMISSIONS: self._fix_permissions,
            RecoveryAction.INCREASE_RESOURCES: self._increase_resources,
            RecoveryAction.ROLLBACK: self._rollback,
            RecoveryAction.REPROVISION: self._reprovision,
        }

        handler = handlers.get(action)
        if not handler:
            return RecoveryResult(
                action_taken=action,
                details=f"No handler for action: {action.value}",
            )

        try:
            return handler(deployment, diagnostics)
        except Exception as exc:
            return RecoveryResult(
                action_taken=action,
                details=f"Handler exception: {exc}",
            )

    def _restart_container(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Restart the failed container."""
        container_name = getattr(deployment, "container_id", "") or ""
        if not container_name:
            service_name = getattr(deployment.service, "name", "") if hasattr(deployment, "service") else ""
            container_name = service_name

        if not container_name:
            return RecoveryResult(
                action_taken=RecoveryAction.RESTART_CONTAINER,
                details="No container name available",
            )

        self._log(f"Restarting container: {container_name}")
        out, err, code = self._exec(
            f"docker restart {shlex.quote(container_name)}",
            timeout=RECOVERY_TIMEOUT,
        )

        time.sleep(POST_RECOVERY_WAIT)

        if code == 0:
            status = self._verify_container_running(container_name)
            if status:
                return RecoveryResult(
                    action_taken=RecoveryAction.RESTART_CONTAINER,
                    success=True,
                    details=f"Container {container_name} restarted successfully",
                    post_recovery_status=status,
                )

        out2, _, _ = self._exec(
            f"docker inspect {shlex.quote(container_name)} --format '{{{{.State.Status}}}}'",
            timeout=10,
        )
        return RecoveryResult(
            action_taken=RecoveryAction.RESTART_CONTAINER,
            details=f"Restart failed or container not running. State: {out2.strip()}",
            next_action=RecoveryAction.RESTART_STACK,
        )

    def _restart_stack(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Restart the entire docker-compose stack."""
        self._log("Restarting entire stack via docker compose up -d")

        try:
            ssh = self._get_ssh()
            success, output = ssh.restart_stack(self._get_hosting_path())
        except Exception as exc:
            success = False
            output = str(exc)

        time.sleep(POST_RECOVERY_WAIT)

        if success:
            container_name = getattr(deployment, "container_id", "") or ""
            status = self._verify_container_running(container_name) if container_name else "stack restarted"
            return RecoveryResult(
                action_taken=RecoveryAction.RESTART_STACK,
                success=True,
                details="Stack restarted successfully",
                post_recovery_status=status,
            )

        return RecoveryResult(
            action_taken=RecoveryAction.RESTART_STACK,
            details=f"Stack restart failed: {output[:500]}",
            next_action=RecoveryAction.REPROVISION,
        )

    def _rebuild_container(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Rebuild and redeploy a specific service."""
        service_name = getattr(deployment.service, "name", "") if hasattr(deployment, "service") else ""
        if not service_name:
            return RecoveryResult(
                action_taken=RecoveryAction.REBUILD_CONTAINER,
                details="No service name available for rebuild",
            )

        self._log(f"Rebuilding service: {service_name}")
        cmd = self._compose_cmd(f"up -d --build {shlex.quote(service_name)}")
        out, err, code = self._exec(cmd, timeout=RECOVERY_TIMEOUT)

        time.sleep(POST_RECOVERY_WAIT)

        if code == 0:
            status = self._verify_container_running(service_name)
            if status:
                return RecoveryResult(
                    action_taken=RecoveryAction.REBUILD_CONTAINER,
                    success=True,
                    details=f"Service {service_name} rebuilt successfully",
                    post_recovery_status=status,
                )

        return RecoveryResult(
            action_taken=RecoveryAction.REBUILD_CONTAINER,
            details=f"Rebuild failed: {(out + err)[:500]}",
            next_action=RecoveryAction.RESTART_STACK,
        )

    def _prune_images(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Prune unused Docker images to free disk space."""
        self._log("Pruning unused Docker images")
        out, err, code = self._exec("docker image prune -af", timeout=RECOVERY_TIMEOUT)

        if code == 0:
            reclaimed = self._extract_reclaimed_space(out)
            return RecoveryResult(
                action_taken=RecoveryAction.PRUNE_IMAGES,
                success=True,
                details=f"Pruned images, reclaimed: {reclaimed}",
            )

        return RecoveryResult(
            action_taken=RecoveryAction.PRUNE_IMAGES,
            details=f"Image prune failed: {(out + err)[:500]}",
        )

    def _prune_volumes(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Prune unused Docker volumes (use with caution)."""
        self._log("Pruning unused Docker volumes")
        out, err, code = self._exec("docker volume prune -f", timeout=RECOVERY_TIMEOUT)

        if code == 0:
            return RecoveryResult(
                action_taken=RecoveryAction.PRUNE_VOLUMES,
                success=True,
                details="Pruned unused volumes",
            )

        return RecoveryResult(
            action_taken=RecoveryAction.PRUNE_VOLUMES,
            details=f"Volume prune failed: {(out + err)[:500]}",
        )

    def _fix_network(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Attempt to fix network connectivity issues."""
        self._log("Attempting network fix")

        out1, _, code1 = self._exec("docker network ls --format '{{.Name}}' | grep smsly", timeout=10)
        networks = [n.strip() for n in out1.strip().split("\n") if n.strip()]

        if not networks:
            self._log("No smsly networks found, checking docker network")
            out2, _, code2 = self._exec("docker network ls --format '{{.Name}}'", timeout=10)
            networks = [n.strip() for n in out2.strip().split("\n") if n.strip()]

        for net in networks:
            self._log(f"Reconnecting network: {net}")
            self._exec(
                f"docker network disconnect {shlex.quote(net)} backend 2>/dev/null; "
                f"docker network connect {shlex.quote(net)} backend 2>/dev/null",
                timeout=30,
            )

        out, _, code = self._exec("ping -c 1 -W 3 8.8.8.8", timeout=10)
        if code == 0:
            return RecoveryResult(
                action_taken=RecoveryAction.FIX_NETWORK,
                success=True,
                details="Network connectivity restored",
            )

        return RecoveryResult(
            action_taken=RecoveryAction.FIX_NETWORK,
            details="Network fix attempted but connectivity not restored",
        )

    def _fix_permissions(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Fix common permission issues."""
        path = self._get_hosting_path()
        self._log(f"Fixing permissions on {path}")

        out, err, code = self._exec(
            f"chmod -R 755 {shlex.quote(path)} 2>&1; "
            f"chown -R 1000:1000 {shlex.quote(path)}/volumes 2>/dev/null",
            timeout=60,
        )

        if code == 0:
            return RecoveryResult(
                action_taken=RecoveryAction.FIX_PERMISSIONS,
                success=True,
                details="Permissions fixed",
            )

        return RecoveryResult(
            action_taken=RecoveryAction.FIX_PERMISSIONS,
            details=f"Permission fix failed: {(out + err)[:500]}",
        )

    def _increase_resources(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Suggest resource increase (cannot auto-scale, but can update service model)."""
        service = getattr(deployment, "service", None)
        if not service:
            return RecoveryResult(
                action_taken=RecoveryAction.INCREASE_RESOURCES,
                details="No service object available",
            )

        old_memory = service.memory_mb or 512
        new_memory = min(old_memory * 2, 16384)
        service.memory_mb = new_memory
        service.save(update_fields=["memory_mb"])

        self._log(f"Increased memory: {old_memory}MB -> {new_memory}MB")
        return RecoveryResult(
            action_taken=RecoveryAction.INCREASE_RESOURCES,
            success=True,
            details=f"Memory increased from {old_memory}MB to {new_memory}MB",
            next_action=RecoveryAction.RESTART_CONTAINER,
        )

    def _rollback(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Rollback to previous deployment (requires previous deployment to exist)."""
        self._log("Attempting rollback")

        from .models_core import Deployment as DeploymentModel

        service = getattr(deployment, "service", None)
        if not service:
            return RecoveryResult(
                action_taken=RecoveryAction.ROLLBACK,
                details="No service for rollback",
            )

        previous = (
            DeploymentModel.objects.filter(
                service=service,
                status=DeploymentModel.Status.ACTIVE,
            )
            .exclude(id=deployment.id)
            .order_by("-created_at")
            .first()
        )

        if not previous:
            return RecoveryResult(
                action_taken=RecoveryAction.ROLLBACK,
                details="No previous deployment to rollback to",
            )

        self._log(f"Rolling back to deployment {previous.id}")
        try:
            from .tasks import smart_deploy_task
            new_deployment = DeploymentModel.objects.create(
                service=service,
                commit_hash=previous.commit_hash or "HEAD",
                status=DeploymentModel.Status.QUEUED,
                commit_message="Auto-rollback after failure",
            )
            provider = service.provider
            if provider:
                smart_deploy_task.delay(
                    deployment_id=str(new_deployment.id),
                    provider_id=str(provider.id),
                    skip_review=True,
                )
                return RecoveryResult(
                    action_taken=RecoveryAction.ROLLBACK,
                    success=True,
                    details=f"Rollback deployment {new_deployment.id} queued",
                )
        except Exception as exc:
            return RecoveryResult(
                action_taken=RecoveryAction.ROLLBACK,
                details=f"Rollback failed: {exc}",
            )

        return RecoveryResult(
            action_taken=RecoveryAction.ROLLBACK,
            details="Rollback could not be initiated",
        )

    def _reprovision(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Trigger full reprovisioning of the node."""
        self._log("Triggering node reprovision")

        out, err, code = self._exec(
            f"cd {shlex.quote(self._get_hosting_path())} && "
            f"(docker compose down && docker compose up -d 2>&1 "
            f"|| docker-compose down && docker-compose up -d 2>&1)",
            timeout=RECOVERY_TIMEOUT * 2,
        )

        time.sleep(POST_RECOVERY_WAIT * 2)

        if code == 0:
            return RecoveryResult(
                action_taken=RecoveryAction.REPROVISION,
                success=True,
                details="Node reprovisioned successfully",
            )

        return RecoveryResult(
            action_taken=RecoveryAction.REPROVISION,
            details=f"Reprovision failed: {(out + err)[:500]}",
            next_action=RecoveryAction.ESCALATE_TO_AI,
        )

    # ─── Verification ────────────────────────────────────────────────

    def _verify_container_running(self, container_name: str) -> Optional[str]:
        """Verify a container is running and return its status."""
        out, _, code = self._exec(
            f"docker inspect {shlex.quote(container_name)} "
            f"--format '{{{{.State.Status}}}}|{{{{.State.Health.Status}}}}'",
            timeout=10,
        )
        if code == 0 and out.strip():
            parts = out.strip().split("|")
            state = parts[0]
            health = parts[1] if len(parts) > 1 else "none"
            if state == "running":
                return f"running (health: {health})"
        return None

    def _extract_reclaimed_space(self, output: str) -> str:
        """Extract reclaimed space from docker prune output."""
        match = re.search(r"Total reclaimed space:\s*(.+)", output)
        return match.group(1).strip() if match else "unknown"

    # ─── AI Escalation ───────────────────────────────────────────────

    def escalate_to_ai(self, deployment, diagnostics: DiagnosticResult) -> dict:
        """
        Escalate to system intelligence (AI) for advanced diagnosis.

        Gathers all diagnostic context and sends to the AI router for
        analysis and remediation commands.
        """
        self._log("Escalating to system intelligence (AI)")

        context = {
            "server": {
                "name": self.server.name,
                "host": self.server.host,
                "is_lite_agent": getattr(self.server, "is_lite_agent", False),
            },
            "deployment": {
                "id": str(getattr(deployment, "id", "")),
                "status": getattr(deployment, "status", ""),
                "container_id": getattr(deployment, "container_id", ""),
            },
            "diagnostics": {
                "failure_type": diagnostics.failure_type.value,
                "container_state": diagnostics.container_state,
                "container_logs": diagnostics.container_logs[-5000:],
                "disk_usage_pct": diagnostics.disk_usage_pct,
                "memory_usage_pct": diagnostics.memory_usage_pct,
                "docker_running": diagnostics.docker_running,
                "network_reachable": diagnostics.network_reachable,
                "error_details": diagnostics.error_details,
            },
            "heal_log": self._heal_log[-20:],
        }

        try:
            # Note: AIProviderSettings is not available in agent mode
            try:
                from apps.intelligence.models import AIProviderSettings
            except (ImportError, RuntimeError):
                self._log("Intelligence app not available in agent mode — cannot escalate to AI")
                return {"success": False, "error": "Intelligence app not available in agent mode"}

            ai_settings = AIProviderSettings.get_solo()
            has_api_key = bool(
                ai_settings.openai_api_key or ai_settings.grok_api_key
                or ai_settings.gemini_api_key or ai_settings.claude_api_key
                or ai_settings.deepseek_api_key or ai_settings.openrouter_api_key
                or ai_settings.groq_api_key or ai_settings.alibaba_api_key
                or ai_settings.jules_api_key or ai_settings.localllm_api_key
                or ai_settings.smslycloud_api_key
            )
            if not has_api_key:
                self._log("No active AI provider — cannot escalate")
                return {"success": False, "error": "No active AI provider"}

            prompt = self._build_ai_prompt(context)

            try:
                from apps.deployments.ai_router import get_ollama_model_name
                import requests as http_requests

                model = get_ollama_model_name() or "llama3"
                base = getattr(ai_settings, "api_base_url", "http://localhost:11434")

                response = http_requests.post(
                    f"{base.rstrip('/')}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                    },
                    timeout=120,
                )

                if response.status_code == 200:
                    data = response.json()
                    ai_response = data.get("response", "")
                    self._log(f"AI response received ({len(ai_response)} chars)")

                    commands = self._extract_commands(ai_response)
                    return {
                        "success": True,
                        "ai_response": ai_response,
                        "suggested_commands": commands,
                    }

            except Exception as exc:
                self._log(f"AI escalation failed: {exc}")

        except Exception as exc:
            self._log(f"AI escalation setup failed: {exc}")

        return {"success": False, "error": "AI escalation failed"}

    def _build_ai_prompt(self, context: dict) -> str:
        """Build a prompt for the AI with full diagnostic context."""
        return f"""You are an expert DevOps engineer diagnosing a failed deployment on a remote server.

SERVER: {context['server']['name']} ({context['server']['host']})
DEPLOYMENT ID: {context['deployment']['id']}
FAILURE TYPE: {context['diagnostics']['failure_type']}

CONTAINER STATE: {context['diagnostics']['container_state']}
DISK USAGE: {context['diagnostics']['disk_usage_pct']}%
MEMORY USAGE: {context['diagnostics']['memory_usage_pct']}%
DOCKER RUNNING: {context['diagnostics']['docker_running']}
NETWORK REACHABLE: {context['diagnostics']['network_reachable']}

CONTAINER LOGS (last 5000 chars):
{context['diagnostics']['container_logs']}

HEAL ATTEMPTS ALREADY MADE:
{chr(10).join('- ' + l for l in context['heal_log'][-10:])}

Analyze the issue and provide:
1. Root cause diagnosis
2. Specific shell commands to fix the issue (one per line, prefixed with CMD:)
3. Verification commands to confirm the fix worked

The server uses Docker Compose. The hosting path is likely /opt/smsly-hosting.
Be specific and actionable. Focus on commands that can be run via SSH.
"""

    def _extract_commands(self, ai_response: str) -> list[str]:
        """Extract commands from AI response."""
        commands = []
        for line in ai_response.split("\n"):
            line = line.strip()
            if line.upper().startswith("CMD:"):
                commands.append(line[4:].strip())
            elif line.startswith("$ ") or line.startswith("# "):
                commands.append(line[2:].strip())
        return commands

    # ─── Quick Commands (for manual/API use) ─────────────────────────

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
