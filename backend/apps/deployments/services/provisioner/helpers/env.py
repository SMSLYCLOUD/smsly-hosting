import os
import re
import shlex
from urllib.parse import urlparse


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


PROVISION_TIMEOUT_SECONDS = _env_int(
    "SMSLY_PROVISION_TIMEOUT_SECONDS",
    1800,
    minimum=60,
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _installer_logs_confirm_success(logs: str) -> bool:
    text = logs or ""
    if "INSTALLATION FAILED" in text:
        return False
    return bool(
        "INSTALLATION SUCCESSFUL!" in text
        or re.search(r"All\s+\d+/\d+\s+verification checks passed", text)
    )


def _shell_env_assignments(values: dict[str, object]) -> str:
    parts = []
    for key, value in values.items():
        if value is None:
            continue
        parts.append(f"{key}={shlex.quote(str(value))}")
    return " ".join(parts)


def _url_password(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    try:
        return urlparse(raw_url).password or ""
    except Exception:
        return ""


def _url_username(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    try:
        return urlparse(raw_url).username or ""
    except Exception:
        return ""
