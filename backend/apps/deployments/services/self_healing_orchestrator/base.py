import contextlib
import logging
import shlex
import time

from django.core.cache import cache

from apps.deployments.constants import (
    SELF_HEAL_TTL,
    SELF_HEAL_MAX_ATTEMPTS,
    SELF_HEAL_SSH_TIMEOUT,
    SELF_HEAL_CONNECT_TIMEOUT,
    SELF_HEAL_COMMAND_TIMEOUT,
    SELF_HEAL_COOLDOWN,
)

from ..ssh_client import SSHClient
from .models import DiagnosticResult

logger = logging.getLogger(__name__)

HEAL_STATE_TTL = SELF_HEAL_TTL
MAX_HEAL_ATTEMPTS = SELF_HEAL_MAX_ATTEMPTS
HEAL_COOLDOWN_SECONDS = SELF_HEAL_SSH_TIMEOUT
DIAGNOSTIC_TIMEOUT = SELF_HEAL_CONNECT_TIMEOUT
RECOVERY_TIMEOUT = SELF_HEAL_COMMAND_TIMEOUT
POST_RECOVERY_WAIT = SELF_HEAL_COOLDOWN


class BaseOrchestratorMixin:
    def __init__(self, server):
        try:
            from .models.core import ManagedServer
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
        if len(self._heal_log) > 1000:
            self._heal_log = self._heal_log[-500:]
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
            wg_address=self.server.wg_address,
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
            with contextlib.suppress(Exception):
                self._ssh.close()
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
        try:
            # Atomic INCR when the backend supports it (Redis/Memcached).
            count = cache.incr(key)
        except (ValueError, AttributeError):
            # Fallback for backends that don't support incr (e.g. LocMem).
            count = (int(cache.get(key, 0) or 0) + 1)
            cache.set(key, count, timeout=HEAL_STATE_TTL)
        if count == 1:
            cache.set(key, count, timeout=HEAL_STATE_TTL)  # set initial TTL
        return count

    def _reset_heal_state(self, scope: str):
        """Reset heal tracking for this scope (called on success)."""
        cache.delete(f"selfheal:attempts:{scope}")
        cache.delete(f"selfheal:cooldown:{scope}")
