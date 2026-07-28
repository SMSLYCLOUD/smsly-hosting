from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import tempfile
from contextlib import suppress

import docker

from apps.deployments.constants import DEPLOY_CONTAINER_TIMEOUT
from apps.deployments.services.ai_router import (
    generate_ai_router_proxy_config,
    get_ollama_model_name,
    is_ai_router_service,
    is_ollama_service,
)
from apps.deployments.models import Service
from apps.deployments.utils import append_log

from .health import _wait_for_local_container_healthy

logger = logging.getLogger(__name__)

def _docker_safe_segment(value: str, fallback: str = "app") -> str:
    slug = re.sub(r"[^a-z0-9_.-]+", "-", str(value or "").lower()).strip("-.")
    if not slug:
        slug = fallback
    return slug[:63]

def _detect_exposed_port(service, image_name: str | None = None) -> int | None:
    try:
        client = docker.from_env()
        exposed = None

        if image_name:
            try:
                img = client.images.get(image_name)
                exposed = img.attrs.get('Config', {}).get('ExposedPorts', {})
            except docker.errors.ImageNotFound:
                pass

        if not exposed:
            last_dep = service.deployments.filter(
                container_id__isnull=False
            ).order_by('-created_at').first()
            if last_dep:
                if last_dep.container_id:
                    try:
                        container = client.containers.get(last_dep.container_id)
                        exposed = container.image.attrs.get('Config', {}).get('ExposedPorts', {})
                    except docker.errors.NotFound:
                        pass
                if not exposed and last_dep.image_name:
                    try:
                        img = client.images.get(last_dep.image_name)
                        exposed = img.attrs.get('Config', {}).get('ExposedPorts', {})
                    except docker.errors.ImageNotFound:
                        pass

        if exposed:
            for port_spec in exposed:
                port_num = port_spec.split('/')[0]
                if port_num.isdigit():
                    return int(port_num)
    except Exception as exc:
        logger.debug("Port auto-detect failed: %s", exc)
    return None

def _run_managed_image_post_deploy_hooks(deployment, service: Service, container_id: str) -> None:
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        container_name = container.name
    except Exception as exc:
        append_log(deployment, f"[hook] Skipped managed-image hooks: {exc}\n")
        return

    env_map = {ev.key: ev.value for ev in service.env_vars.all()}


    if str(env_map.get("RUN_PRISMA_MIGRATE", "")).strip().lower() in {"1", "true", "yes"}:
        append_log(deployment, "\n[hook] Running Prisma migrate deploy inside container...\n")
        prisma_res = subprocess.run(
            ["docker", "exec", container_name, "sh", "-lc", "cd /app && npx prisma migrate deploy"],
            capture_output=True,
            text=True,
            timeout=DEPLOY_CONTAINER_TIMEOUT,
        )
        if prisma_res.returncode == 0:
            append_log(deployment, "[hook] Prisma migrate deploy succeeded.\n")
        else:
            append_log(
                deployment,
                "[hook] Prisma migrate deploy failed:\n"
                f"{prisma_res.stdout}\n{prisma_res.stderr}\n",
            )

    if is_ollama_service(service):
        model_name = get_ollama_model_name(service) or str(env_map.get("OLLAMA_MODEL", "")).strip()
        if model_name:
            append_log(deployment, f"\n[hook] Pulling Ollama model `{model_name}` inside {container_name}...\n")
            pull_res = subprocess.run(
                ["docker", "exec", container_name, "sh", "-lc", f"ollama pull {shlex.quote(model_name)}"],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if pull_res.returncode == 0:
                append_log(deployment, f"[hook] Ollama model `{model_name}` is ready.\n")
            else:
                append_log(
                    deployment,
                    "[hook] Ollama model pull failed:\n"
                    f"{pull_res.stdout}\n{pull_res.stderr}\n",
                )

    if not is_ai_router_service(service):
        return

    config_text = generate_ai_router_proxy_config(service)
    with tempfile.NamedTemporaryFile("w", suffix="-ai-router.yaml", delete=False, encoding="utf-8") as handle:
        handle.write(config_text)
        config_path = handle.name

    try:
        append_log(deployment, "\n[hook] Syncing LiteLLM router catalog...\n")
        copy_res = subprocess.run(
            ["docker", "cp", config_path, f"{container_name}:/app/proxy_server_config.yaml"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if copy_res.returncode != 0:
            raise RuntimeError(
                "Failed to copy router config:\n"
                f"{copy_res.stdout}\n{copy_res.stderr}"
            )

        restart_res = subprocess.run(
            ["docker", "restart", container_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if restart_res.returncode != 0:
            raise RuntimeError(
                "Failed to restart router container after config sync:\n"
                f"{restart_res.stdout}\n{restart_res.stderr}"
            )

        if not _wait_for_local_container_healthy(deployment, container_id, timeout_seconds=180):
            raise RuntimeError("Router restart completed but health did not recover in time")

        append_log(deployment, "[hook] LiteLLM router catalog synced.\n")
    finally:
        with suppress(OSError):
            os.unlink(config_path)
