import logging
import re
import shlex
import time
from collections.abc import Callable

from django.conf import settings

from .base import MAX_HEAL_ATTEMPTS, RECOVERY_TIMEOUT, POST_RECOVERY_WAIT
from .enums import FailureType, RecoveryAction
from .models import DiagnosticResult, RecoveryResult

logger = logging.getLogger(__name__)


class RecoveryMixin:

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
        handlers: dict[RecoveryAction, Callable[..., RecoveryResult]] = {
            RecoveryAction.RESTART_CONTAINER: self._restart_container,
            RecoveryAction.RESTART_STACK: self._restart_stack,
            RecoveryAction.RESTART_DOCKER_DAEMON: self._restart_docker_daemon,
            RecoveryAction.REBUILD_CONTAINER: self._rebuild_container,
            RecoveryAction.REPAIR_BUILDX: self._repair_buildx,
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
        _out, _err, code = self._exec(
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

    def _restart_docker_daemon(self, deployment, diagnostics: DiagnosticResult) -> RecoveryResult:
        """Restart the Docker daemon on the remote node via SSH (systemctl)."""
        self._log("Restarting Docker daemon via systemctl")

        out, err, code = self._exec(
            "systemctl restart docker 2>&1 && sleep 3 && docker info --format '{{.ServerVersion}}'",
            timeout=RECOVERY_TIMEOUT,
        )

        if code == 0:
            self._log(f"Docker daemon restarted successfully (version: {out.strip()[:50]})")
            return RecoveryResult(
                action_taken=RecoveryAction.RESTART_DOCKER_DAEMON,
                success=True,
                details="Docker daemon restarted successfully",
                post_recovery_status=f"Docker {out.strip()[:50]}",
                next_action=RecoveryAction.RESTART_STACK,
            )

        return RecoveryResult(
            action_taken=RecoveryAction.RESTART_DOCKER_DAEMON,
            details=f"Docker daemon restart failed: {out} {err}"[:500],
            next_action=RecoveryAction.REPROVISION,
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
                post_recovery_status=status or "stack restarted",
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

        # First try docker-compose (for services defined in compose files)
        cmd = self._compose_cmd(f"up -d --build {shlex.quote(service_name)}")
        _out, _err, code = self._exec(cmd, timeout=RECOVERY_TIMEOUT)

        if code == 0:
            time.sleep(POST_RECOVERY_WAIT)
            status = self._verify_container_running(service_name)
            if status:
                return RecoveryResult(
                    action_taken=RecoveryAction.REBUILD_CONTAINER,
                    success=True,
                    details=f"Service {service_name} rebuilt via compose",
                    post_recovery_status=status,
                )

        # If compose failed, try pulling image from local registry and running directly
        docker_image = getattr(deployment.service, "docker_image", "") or ""
        if docker_image:
            # The stored ref is qualified with the master-INTERNAL
            # registry address (registry:5000 / loopback) which does NOT
            # resolve on a remote node. Rewrite to the node-routable
            # address (WG mesh IP / public IP) — on the master itself
            # the rewrite is a no-op when no routable address is
            # configured (single-host installs).
            from apps.deployments.services.registry_routing import image_ref_for_node
            docker_image = image_ref_for_node(docker_image)
        if not docker_image:
            # Construct image name from the platform's configured
            # CONTAINER_REGISTRY_URL via the centralised registry
            # validation helper. The helper guarantees the
            # resulting host:port is on the platform allowlist
            # (loopback, public, or master mesh) and never
            # includes user-controlled input in the registry host
            # portion.
            commit = getattr(deployment, "commit_hash", "") or ""
            tag = commit[:8] if commit else "latest"
            from ..registry_validation import safe_image_for_service
            docker_image = safe_image_for_service(service_name, tag=tag)

        self._log(f"Pulling image: {docker_image}")
        pull_out, pull_err, pull_code = self._exec(
            f"docker pull {shlex.quote(docker_image)}", timeout=RECOVERY_TIMEOUT,
        )

        if pull_code != 0:
            return RecoveryResult(
                action_taken=RecoveryAction.REBUILD_CONTAINER,
                details=f"Compose failed and image pull failed: {(pull_out + pull_err)[:400]}",
                next_action=RecoveryAction.ESCALATE_TO_AI,
            )

        # Remove old container if it exists
        self._exec(f"docker rm -f {shlex.quote(service_name)}", timeout=30)

        # Detect sandboxed container runtime (gVisor/Kata)
        from ..container_runtime import get_runtime_for_container
        runtime = get_runtime_for_container(service_name=service_name)
        runtime_flag = f"--runtime {runtime}" if runtime else ""

        # Run the container with the same network as the compose stack
        port = getattr(deployment.service, "port", 8000) or 8000
        mem_mb = getattr(deployment.service, "memory_mb", 2048) or 2048
        cpus = getattr(deployment.service, "cpu_cores", 1.0) or 1.0
        sec_flags = (
            "--security-opt no-new-privileges:true --security-opt apparmor=docker-default "
            "--cap-drop=ALL --cap-add=NET_BIND_SERVICE --cap-add=CHOWN --cap-add=SETUID --cap-add=SETGID "
            f"--memory={mem_mb}m --cpus={cpus} --pids-limit=1024 "
        )
        run_cmd = (
            f"docker run -d --name {shlex.quote(service_name)} "
            f"{sec_flags}"
            f"{runtime_flag} "
            f"--network smsly-net --restart unless-stopped "
            f"-p {port}:{port} "
            f"{shlex.quote(docker_image)}"
        )
        self._log(f"Starting container: {run_cmd}")
        run_out, run_err, run_code = self._exec(run_cmd, timeout=RECOVERY_TIMEOUT)

        time.sleep(POST_RECOVERY_WAIT)

        if run_code == 0:
            status = self._verify_container_running(service_name)
            if status:
                return RecoveryResult(
                    action_taken=RecoveryAction.REBUILD_CONTAINER,
                    success=True,
                    details=f"Service {service_name} redeployed from image {docker_image}",
                    post_recovery_status=status,
                )

        return RecoveryResult(
            action_taken=RecoveryAction.REBUILD_CONTAINER,
            details=f"Rebuild failed: {(run_out + run_err)[:500]}",
            next_action=RecoveryAction.ESCALATE_TO_AI,
        )

    def _repair_buildx(
        self,
        deployment=None,
        diagnostics: DiagnosticResult | None = None,
    ) -> RecoveryResult:
        """Self-heal a broken buildx default builder on the node."""
        if not self._can_heal():
            return RecoveryResult(
                action_taken=RecoveryAction.NONE,
                details="No SSH credentials available",
            )

        try:
            fallback = getattr(
                settings, "BUILDX_FALLBACK_BUILDER", "smsly-fallback"
            ) or "smsly-fallback"

            self._log(f"Repairing buildx on node (fallback={fallback!r})")

            # 1. Make sure the fallback exists, prefer the
            #    docker-container driver.
            create_out, _, _ = self._exec(
                f"docker buildx create --name {fallback} "
                f"--driver docker-container --use 2>&1 || true",
                timeout=60,
            )
            self._log(f"buildx create fallback: {create_out.strip()[:200]}")

            # 2. Switch to the fallback as the active context so
            #    the default can be removed.
            self._exec(
                f"docker context use {fallback} 2>&1 || true",
                timeout=15,
            )

            # 3. Try to remove the default context and builder.
            #    Errors here are non-fatal: the fallback is now
            #    active and the corrupt default is no longer
            #    blocking builds.
            rm_ctx_out, _, _ = self._exec(
                "docker context rm default 2>&1 || true",
                timeout=15,
            )
            self._log(f"buildx remove default context: {rm_ctx_out.strip()[:200]}")

            rm_bld_out, _, _ = self._exec(
                "docker buildx rm default 2>&1 || true",
                timeout=15,
            )
            self._log(f"buildx remove default builder: {rm_bld_out.strip()[:200]}")

            # 4. Verify the fallback is healthy.
            ls_out, _, _ = self._exec(
                "docker buildx ls 2>&1",
                timeout=15,
            )
            self._log(f"buildx ls after repair: {ls_out.strip()[:400]}")

            if fallback in (ls_out or ""):
                return RecoveryResult(
                    success=True,
                    action_taken=RecoveryAction.REPAIR_BUILDX,
                    details=(
                        f"Buildx fallback {fallback!r} is active and "
                        "the corrupt default was removed."
                    ),
                )

            return RecoveryResult(
                action_taken=RecoveryAction.REPAIR_BUILDX,
                details=(
                    "Buildx repair attempted but the fallback is "
                    f"not visible in ``docker buildx ls``:\n{ls_out}"
                ),
                next_action=RecoveryAction.ESCALATE_TO_AI,
            )
        except Exception as exc:
            self._log(f"buildx repair exception: {exc}")
            return RecoveryResult(
                action_taken=RecoveryAction.REPAIR_BUILDX,
                details=f"buildx repair exception: {exc}",
                next_action=RecoveryAction.ESCALATE_TO_AI,
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

        out1, _, _code1 = self._exec("docker network ls --format '{{.Name}}' | grep smsly", timeout=10)
        networks = [n.strip() for n in out1.strip().split("\n") if n.strip()]

        if not networks:
            self._log("No smsly networks found, checking docker network")
            out2, _, _code2 = self._exec("docker network ls --format '{{.Name}}'", timeout=10)
            networks = [n.strip() for n in out2.strip().split("\n") if n.strip()]

        for net in networks:
            self._log(f"Reconnecting network: {net}")
            self._exec(
                f"docker network disconnect {shlex.quote(net)} backend 2>/dev/null; "
                f"docker network connect {shlex.quote(net)} backend 2>/dev/null",
                timeout=30,
            )

        _out, _, code = self._exec("ping -c 1 -W 3 8.8.8.8", timeout=10)
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

        from ..models.core import Deployment as DeploymentModel

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
            from apps.deployments.tasks.deploy.helpers import enqueue_smart_deploy_task
            new_deployment = DeploymentModel.objects.create(
                service=service,
                commit_hash=previous.commit_hash or "HEAD",
                status=DeploymentModel.Status.QUEUED,
                commit_message="Auto-rollback after failure",
            )
            provider = service.provider
            if provider:
                enqueue_smart_deploy_task(
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

    def _verify_container_running(self, container_name: str) -> str | None:
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
