import contextlib
import logging
import os
import shlex
import subprocess
import tempfile

from apps.deployments.services.ai_router import (
    generate_ai_router_proxy_config,
    get_ollama_model_name,
    is_ai_router_service,
    is_ollama_service,
)
from apps.deployments.utils import append_log, redact_values


logger = logging.getLogger(__name__)


class HookMixin:
    def _post_deploy_hooks(self, container_name: str):
        """Run post-deploy hooks for managed AI images."""
        try:
            env_map = {ev.key: ev.value for ev in self.service.env_vars.all()}
            if env_map.get("RUN_PRISMA_MIGRATE", "").strip().lower() in {"1", "true", "yes"}:
                append_log(self.deployment, "\n[hook] Running Prisma migrate deploy inside container...\n")
                cmd = [
                    "docker", "exec", container_name,
                    "sh", "-lc", "cd /app && npx prisma migrate deploy"
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if res.returncode == 0:
                    append_log(self.deployment, "[hook] Prisma migrate deploy succeeded.\n")
                else:
                    redacted_prisma = redact_values(
                        f"{res.stdout}\n{res.stderr}\n", self.secret_values
                    )
                    append_log(self.deployment, redacted_prisma)

            if is_ollama_service(self.service):
                self._pull_ollama_model(container_name, env_map)

            if is_ai_router_service(self.service):
                self._sync_ai_router_config(container_name)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            append_log(self.deployment, f"[hook] Post-deploy hook skipped: {exc}\n")



    def _pull_ollama_model(self, container_name: str, env_map: dict[str, str]):
        """Ensure Ollama template services download their configured model."""
        model_name = get_ollama_model_name(self.service) or str(env_map.get("OLLAMA_MODEL", "")).strip()
        if not model_name:
            return

        append_log(
            self.deployment,
            f"\n[hook] Pulling Ollama model `{model_name}` inside {container_name}...\n",
        )
        cmd = [
            "docker", "exec", container_name,
            "sh", "-lc", f"ollama pull {shlex.quote(model_name)}"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if res.returncode == 0:
            append_log(self.deployment, f"[hook] Ollama model `{model_name}` is ready.\n")
            return

        append_log(
            self.deployment,
            "[hook] Ollama model pull failed:\n"
            f"{res.stdout}\n{res.stderr}\n"
        )



    def _sync_ai_router_config(self, container_name: str):
        """Write the generated LiteLLM config into the router container and restart it."""
        config_text = generate_ai_router_proxy_config(self.service)
        with tempfile.NamedTemporaryFile(
            "w", suffix="-ai-router.yaml", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(config_text)
            config_path = handle.name

        try:
            append_log(self.deployment, "\n[hook] Syncing LiteLLM router catalog...\n")
            copy_res = subprocess.run(
                ["docker", "cp", config_path, f"{container_name}:/app/proxy_server_config.yaml"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if copy_res.returncode != 0:
                append_log(
                    self.deployment,
                    "[hook] Failed to copy router config:\n"
                    f"{copy_res.stdout}\n{copy_res.stderr}\n",
                )
                return

            restart_res = subprocess.run(
                ["docker", "restart", container_name],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if restart_res.returncode != 0:
                append_log(
                    self.deployment,
                    "[hook] Failed to restart router container after config sync:\n"
                    f"{restart_res.stdout}\n{restart_res.stderr}\n",
                )
                return

            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            client.containers.get(container_name)
            # Simple health poll — the compose adapter isn't available here.
            import time as _time
            deadline = _time.monotonic() + 180
            healthy = False
            while _time.monotonic() < deadline:
                try:
                    c = client.containers.get(container_name)
                    if c.status == 'running':
                        healthy = True
                        break
                except Exception:
                    pass
                _time.sleep(3)
            if healthy:
                append_log(self.deployment, "[hook] LiteLLM router catalog synced.\n")
            else:
                append_log(self.deployment, "[hook] Router restart completed but health did not recover in time.\n")
        finally:
            with contextlib.suppress(OSError):
                os.unlink(config_path)

