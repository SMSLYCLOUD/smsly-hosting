"""Helper utilities for transfer service."""

import logging
import os
import re

from django.conf import settings

logger = logging.getLogger(__name__)

TRANSFER_LOG_LIMIT = getattr(settings, "TRANSFER_LOG_LIMIT", 100 * 1024)
TRANSFER_ERROR_LIMIT = 4_000


def get_transfer_log_limit():
    return getattr(settings, "TRANSFER_LOG_LIMIT", TRANSFER_LOG_LIMIT)


_TRANSFER_SCRUB_KEYS = frozenset({
    "BACKUP_ENCRYPTION_KEY",
    "GATEWAY_SECRET",
    "CLOUDFLARE_API_TOKEN",
    "SENTRY_DSN",
    "WEBHOOK_SECRET",
    "OAUTH_CLIENT_SECRET",
    "INTERNAL_API_TOKEN",
    "JWT_SIGNING_KEY",
    "GITLAB_SECRET_TOKEN",
    "GITHUB_WEBHOOK_SECRET",
    "BITBUCKET_WEBHOOK_SECRET",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "SMTP_PASSWORD",
    "DATABASE_URL",
    "REDIS_URL",
})


def _scrub_env_for_transfer(path: str) -> str:
    scrubbed_lines = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                scrubbed_lines.append(line)
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", stripped)
            if not m:
                scrubbed_lines.append(line)
                continue
            key, _ = m.group(1), m.group(2)
            if key in _TRANSFER_SCRUB_KEYS:
                scrubbed_lines.append(
                    f"# {key}=<OPERATOR-MUST-SET-AFTER-TRANSFER>  # scrubbed by Batch G"
                )
            else:
                scrubbed_lines.append(line)
    return "\n".join(scrubbed_lines) + "\n"


def _command_text(result) -> str:
    if isinstance(result, tuple):
        stdout = result[0] if len(result) > 0 else ""
        stderr = result[1] if len(result) > 1 else ""
        return (stdout or "") + (("\n" + stderr) if stderr else "")
    return "" if result is None else str(result)


def _safe_service_name(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9 _.-]', '', name)[:255]


def _safe_backup_basename(file_path: str) -> str:
    name = os.path.basename(file_path)
    name = re.sub(r'[^a-zA-Z0-9_.-]', '', name)
    return name[:255]


_PATTERNS = [
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        flags=re.DOTALL,
    ),
    re.compile(
        r"(?i)((?:TOKEN|SECRET|PASSWORD|KEY|DSN|DATABASE_URL|REDIS_URL|AMQP_URL|BROKER_URL|API_KEY)[A-Z0-9_]*=)([^\s\"']+)",
    ),
    re.compile(
        r"Bearer\s+[A-Za-z0-9._-]+",
    ),
    re.compile(
        r"postgres(ql)?://[^\s]+:[^\s]+@",
    ),
    re.compile(
        r"(?i)((?:Authorization|X-API-Key|X-Auth-Token):\s*)(\S+)",
    ),
    re.compile(
        r"(?:https?://)[^:/\s]+:[^@\s]+@",
    ),
]


def _redact_transfer_text(text: str) -> str:
    if not text:
        return ""
    safe = str(text).replace("\x00", "")
    for idx, pat in enumerate(_PATTERNS):
        if idx == 0:
            safe = pat.sub(
                "-----BEGIN PRIVATE KEY-----***-----END PRIVATE KEY-----",
                safe,
            )
        elif idx == 1:
            safe = pat.sub(r"\1***", safe)
        elif idx in (2, 3):
            safe = pat.sub("***", safe)
        elif idx == 4:
            safe = pat.sub(r"\1***", safe)
        else:
            safe = pat.sub("***@", safe)
    return safe
