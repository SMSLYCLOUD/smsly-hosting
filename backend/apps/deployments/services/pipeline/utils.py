import logging
import os
import tempfile

logger = logging.getLogger(__name__)


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
