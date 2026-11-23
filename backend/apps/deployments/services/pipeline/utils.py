import logging
import os
import posixpath
import re
import tempfile

logger = logging.getLogger(__name__)

_SAFE_COMPOSE_REL_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_SAFE_COMPOSE_SVC_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def normalize_compose_file_path(value: str) -> str:
    """Validate and normalize a relative compose file path inside the repo."""
    if not isinstance(value, str):
        raise ValueError("compose_file must be a string.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("compose_file cannot be empty.")
    if "\x00" in cleaned or "\\" in cleaned:
        raise ValueError("compose_file contains invalid characters.")
    if cleaned.startswith("/") or not _SAFE_COMPOSE_REL_RE.match(cleaned):
        raise ValueError(
            "compose_file must be a safe relative path using only letters, numbers, '.', '_', '-', and '/'."
        )
    normalized = posixpath.normpath(cleaned)
    parts = [p for p in normalized.split("/") if p]
    if not parts or normalized in (".", "..") or normalized.startswith("/") or ".." in parts:
        raise ValueError("compose_file must stay within the repository directory.")
    return normalized


def resolve_compose_path(base_dir: str, compose_file: str) -> str:
    """Resolve compose_file under base_dir safely, blocking traversal."""
    normalized = normalize_compose_file_path(compose_file or "docker-compose.yml")
    base_real = os.path.realpath(base_dir)
    target_real = os.path.realpath(os.path.join(base_real, normalized))
    if target_real != base_real and not target_real.startswith(base_real + os.sep):
        raise ValueError("compose_file resolves outside the build workspace.")
    return target_real


def _is_dir_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".perm_probe_{os.getpid()}_{id(path)}")
        with open(probe, "w") as f:
            f.write("ok")
        try:
            os.remove(probe)
        except OSError:
            pass
        return True
    except (OSError, PermissionError):
        return False


def _resolve_builds_root():
    explicit = os.environ.get('SMSLY_BUILDS_DIR')
    if explicit and _is_dir_writable(explicit):
        return explicit
    preferred = '/opt/smsly-hosting/builds'
    if _is_dir_writable(preferred):
        return preferred
    fallback = os.path.join(tempfile.gettempdir(), 'smsly-builds')
    try:
        os.makedirs(fallback, exist_ok=True)
    except OSError:
        pass
    return fallback


def _get_builds_root():
    root = getattr(_get_builds_root, '_cached', None)
    if root is None or not _is_dir_writable(root):
        root = _resolve_builds_root()
        _get_builds_root._cached = root
    return root


_BUILDS_ROOT = _get_builds_root()


def _read_env_file(path):
    with open(path) as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            yield stripped
