import logging
import shlex

from django.conf import settings

from ..helpers import _command_text

logger = logging.getLogger(__name__)


class ExecMixin:

    def _exec_on_target(self, script, container='backend', timeout=120):
        return self._node_api_request('incoming/exec', body={
            'script': script,
            'container': container,
        }, timeout=timeout)

    def _find_remote_backend_container(self, required=False):
        configured = getattr(
            settings, "REMOTE_BACKEND_CONTAINER_NAME", "smsly-hosting-backend-1"
        )
        candidates = []

        for cmd in (
            "docker ps --filter name=backend --format '{{.Names}}'",
            f"docker ps --filter name={shlex.quote(configured)} --format '{{{{.Names}}}}'",
        ):
            output = _command_text(
                self.ssh.exec_command(cmd, raise_on_error=False)
            ).strip()
            for raw_name in output.splitlines():
                name = raw_name.strip("'\" ")
                if name and name not in candidates:
                    candidates.append(name)

        for name in candidates:
            if 'hosting' in name and 'backend' in name:
                return name
        for name in candidates:
            if 'backend' in name:
                return name

        if required:
            raise RuntimeError(
                "Could not locate Grid backend container on target server. "
                f"Searched for: {candidates or [configured]}"
            )
        return None

    def _ensure_target_platform_started(self):
        hosting_path = self.ssh.find_hosting_path()
        safe_path = shlex.quote(hosting_path)
        timeout = int(getattr(settings, "TRANSFER_TARGET_START_TIMEOUT", 1200))
        agent_lite = "infrastructure/docker/docker-compose.agent-lite.yml"
        cmd = " && ".join([
            f"cd {safe_path}",
            "mkdir -p caddy-config /opt/smsly-cache",
            "docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null",
            "docker network inspect smsly-proxy >/dev/null 2>&1 || docker network create smsly-proxy >/dev/null",
            "("
            f"test -f {shlex.quote(agent_lite)} "
            f"&& docker compose -f {shlex.quote(agent_lite)} up -d --build"
            " || ("
            "test -f docker-compose.prod.yml "
            "&& docker compose -f docker-compose.prod.yml up -d --build"
            " || docker compose up -d --build"
            ")"
            ")",
        ])
        self._update(8, 'Starting Grid platform on target server...')
        self.ssh.exec_command(cmd, timeout=timeout)

    def _wait_for_remote_backend_ready(self, backend_container):
        safe_container = shlex.quote(backend_container)
        command = (
            f"for i in $(seq 1 60); do "
            f"docker exec {safe_container} curl -fsS -m 5 http://localhost:8000/health/live 2>/dev/null "
            f"| grep -q '\"status\": \"alive\"' "
            f"&& echo READY && exit 0; "
            f"docker exec {safe_container} curl -fsS -m 5 http://localhost:8000/health 2>/dev/null "
            f"| grep -q '\"status\": \"healthy\"' "
            f"&& echo READY && exit 0; "
            f"sleep 5; "
            f"done; echo NOT_READY; exit 1"
        )
        output = _command_text(self.ssh.exec_command(command, timeout=330))
        if "READY" not in output:
            raise RuntimeError("Target Grid backend did not become ready before restore.")

    def _target_hosting_path(self) -> str:
        try:
            path = self.ssh.find_hosting_path()
            if isinstance(path, str) and path.startswith("/"):
                return path.rstrip("/")
        except Exception as exc:
            logger.warning("Could not detect target Grid install path: %s", exc)
        return "/opt/smsly-hosting"
