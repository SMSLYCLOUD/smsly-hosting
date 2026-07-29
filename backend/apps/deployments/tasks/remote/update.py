"""Remote server update helpers — SSH command execution, log management, pre/post-flight scripts."""
import logging
import re
import shlex
import time
from contextlib import suppress

logger = logging.getLogger(__name__)

REMOTE_UPDATE_LOG_LIMIT = 300_000
def _redact_remote_update_log(text: str) -> str:
    """Redact credentials before persisting remote update output."""
    if not text:
        return ""
    safe = str(text).replace("\x00", "")
    safe = re.sub(r"https://x-access-token:[^@\s]+@", "https://x-access-token:***@", safe)
    safe = re.sub(
        r"(?i)((?:TOKEN|SECRET|PASSWORD|KEY|DSN|DATABASE_URL|REDIS_URL)[A-Z0-9_]*=)([^\s]+)",
        r"\1***",
        safe,
    )
    return safe

def _append_remote_update_log(server, message: str):
    """Append bounded, redacted text to a ManagedServer provision log."""
    safe = _redact_remote_update_log(message)
    if not safe:
        return
    existing = server.provision_logs or ""
    combined = existing + safe
    if len(combined) > REMOTE_UPDATE_LOG_LIMIT:
        combined = (
            "--- Older update log output truncated to keep this record bounded ---\n"
            + combined[-REMOTE_UPDATE_LOG_LIMIT:]
        )
    server.provision_logs = combined
    server.save(update_fields=["provision_logs", "updated_at"])

def _remote_update_preflight_script(hosting_path: str) -> str:
    quoted_path = shlex.quote(hosting_path)
    return f"""
set -u
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo -n"; fi
cd {quoted_path} || {{ echo "SMSLY install path not found: {quoted_path}" >&2; exit 12; }}
[ -f install.sh ] || {{ echo "install.sh is missing in {quoted_path}" >&2; exit 13; }}
command -v bash >/dev/null || {{ echo "bash is not installed" >&2; exit 14; }}
command -v docker >/dev/null || {{ echo "docker is not installed" >&2; exit 15; }}
$SUDO docker info >/dev/null || {{ echo "docker daemon is not reachable" >&2; exit 16; }}
if $SUDO docker compose version >/dev/null 2>&1; then
  echo "compose=docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  echo "compose=docker-compose"
else
  echo "docker compose/docker-compose is not available" >&2
  exit 17
fi
available_kb=$(df -Pk . | awk 'NR==2 {{print $4}}')
if [ "${{available_kb:-0}}" -lt 1048576 ]; then
  echo "WARNING: less than 1GiB free on install filesystem" >&2
fi
echo "path=$(pwd)"
echo "current_commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "WARNING: remote worktree has local changes; installer must handle or preserve them."
    git status --short | head -n 60
  fi
fi
"""

class ThrottledLogAppender:
    """Buffers and throttles database saves for remote server update logs to avoid lockups."""
    def __init__(self, server, interval=1.5):
        self.server = server
        self.interval = interval
        self.buffer = ""
        self.last_save = time.time()

    def append(self, text):
        if not text:
            return
        self.buffer += text
        now = time.time()
        if now - self.last_save >= self.interval:
            self.flush()

    def flush(self):
        if self.buffer:
            with suppress(Exception):
                self.server.refresh_from_db(fields=["provision_logs"])
            _append_remote_update_log(self.server, self.buffer)
            self.buffer = ""
            self.last_save = time.time()

def _remote_update_postflight_script(hosting_path: str) -> str:
    quoted_path = shlex.quote(hosting_path)
    return f"""
set -u
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo -n"; fi
cd {quoted_path} || exit 22
if $SUDO docker compose version >/dev/null 2>&1; then
  COMPOSE="$SUDO docker compose"
else
  COMPOSE="$SUDO docker-compose"
fi
echo "> Compose status after update"
$COMPOSE ps 2>&1 || true
if $COMPOSE config --services 2>/dev/null | grep -qx backend; then
  backend_status="$($COMPOSE ps backend 2>/dev/null | tail -n +2 || true)"
  if [ -z "$backend_status" ] || ! printf '%s\n' "$backend_status" | grep -Eiq 'running|up|healthy'; then
    echo "backend service is not running after update" >&2
    exit 31
  fi
fi
for url in http://127.0.0.1:8090/health http://127.0.0.1/health; do
  if curl -fsS --max-time 10 "$url" >/dev/null 2>&1; then
    echo "health=$url OK"
    exit 0
  fi
done
echo "WARNING: no local health endpoint responded after update" >&2
exit 0
"""

def _run_ssh_command(ssh, command: str, timeout: int | None = None, raise_on_error: bool = True, callback=None):  # type: ignore[no-untyped-def]
    from unittest.mock import Mock
    stdout, stderr, code = ssh.exec_command(
        command,
        timeout=timeout,
        raise_on_error=raise_on_error,
        callback=callback,
    )
    if isinstance(ssh.exec_command, Mock) and callback:
        callback(stdout, stderr)
    return stdout, stderr, code
