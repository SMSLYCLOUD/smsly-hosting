"""
File, path, and binary lookup utilities.
"""
import glob
import logging
import os
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile

logger = logging.getLogger(__name__)


def extract_dockerfile_arg_names(dockerfile_path: str) -> set[str]:
    arg_names: set[str] = set()
    try:
        with open(dockerfile_path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.upper().startswith("ARG "):
                    continue
                arg_def = line[4:].strip()
                if not arg_def:
                    continue
                name = arg_def.split("=", 1)[0].strip()
                name = name.split()[0].strip()
                if name:
                    arg_names.add(name)
    except Exception:
        return set()
    return arg_names


def redact_values(text: str, values: list[str]) -> str:
    if not text:
        return text

    redacted = text
    for val in values:
        if not val:
            continue
        if len(val) < 4:
            continue
        redacted = redacted.replace(val, "***")

    redacted = re.sub(
        r"(--build-arg\s+(?:[A-Z0-9_]*?(?:SECRET|TOKEN|PASSWORD|KEY|DSN)[A-Z0-9_]*?)=)([^\s]+)",
        r"\1***",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def get_source_root_dir() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def build_local_source_bundle() -> str:
    source_root = get_source_root_dir()
    if not os.path.isdir(source_root):
        raise FileNotFoundError(f"Source root not found: {source_root}")

    fd, archive_path = tempfile.mkstemp(prefix="smsly-src-", suffix=".tar.gz")
    os.close(fd)

    excluded = {
        ".git",
        "node_modules",
        ".next",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        ".credentials",
        ".git-credentials",
        "backups",
        "scratch",
        "media",
    }

    with tarfile.open(archive_path, mode="w:gz") as tar:
        for root, dirs, files in os.walk(source_root, topdown=True):
            dirs[:] = [d for d in dirs if d not in excluded]
            rel_root = os.path.relpath(root, source_root)
            rel_root = "" if rel_root == "." else rel_root

            for filename in files:
                if filename in excluded:
                    continue
                local_path = os.path.join(root, filename)
                rel_path = os.path.join(rel_root, filename) if rel_root else filename
                try:
                    tar.add(local_path, arcname=rel_path, recursive=False)
                except (PermissionError, FileNotFoundError, OSError):
                    continue

    return archive_path


def validate_and_sanitize_path(path: str, skip_system_check: bool = False, container=None) -> str:
    if not path or not isinstance(path, str):
        raise ValueError("Path must be a non-empty string")

    dangerous_patterns = [
        r'\.\./',
        r'\.\.\\',
        r'[/\\]\.\.[/\\]',
        r'^\.\./',
        r'/\.\.$',
        r'[/\\]\.\.$',
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, path):
            raise ValueError(f"Path contains potentially dangerous sequence: {pattern}")

    if '\x00' in path:
        raise ValueError("Path contains null bytes")

    normalized_path = path.replace('\\', '/')
    normalized_path = posixpath.normpath(normalized_path)

    if not normalized_path.startswith('/'):
        normalized_path = '/' + normalized_path

    normalized_path = re.sub(r'/+', '/', normalized_path)

    if len(normalized_path) > 4096:
        raise ValueError("Path is too long")

    dangerous_chars = ['<', '>', '|', '?', '*', '"']
    for char in dangerous_chars:
        if char in normalized_path:
            raise ValueError(f"Path contains dangerous character: {char}")

    if '$' in normalized_path and '{' in normalized_path and '}' in normalized_path:
        raise ValueError("Path contains environment variables")

    def _validate_system_dirs(candidate_path: str):
        if not skip_system_check:
            system_directories = ['/etc', '/usr', '/bin', '/sbin', '/var', '/sys', '/proc', '/dev']
            for sys_dir in system_directories:
                if candidate_path == sys_dir or candidate_path.startswith(sys_dir + '/'):
                    raise ValueError(f"Access to system directory '{sys_dir}' is not allowed")

    _validate_system_dirs(normalized_path)

    if container is not None:
        try:
            exit_code, output = container.exec_run(["readlink", "-f", normalized_path])
            if exit_code == 0:
                resolved_path = output.decode('utf-8', errors='replace').strip()
                if resolved_path:
                    resolved_path = resolved_path.replace('\\', '/')
                    resolved_path = posixpath.normpath(resolved_path)
                    if not resolved_path.startswith('/'):
                        resolved_path = '/' + resolved_path
                    resolved_path = re.sub(r'/+', '/', resolved_path)
                    _validate_system_dirs(resolved_path)

                    if len(resolved_path) > 4096:
                        raise ValueError("Path is too long")

                    for char in dangerous_chars:
                        if char in resolved_path:
                            raise ValueError(f"Path contains dangerous character: {char}")

                    if '$' in resolved_path and '{' in resolved_path and '}' in resolved_path:
                        raise ValueError("Path contains environment variables")

                    normalized_path = resolved_path
        except Exception:
            pass

    return normalized_path


def find_binary(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path
    common_dirs = [
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/local/sbin",
        "/usr/sbin",
        "/sbin",
        "/opt/trivy",
        "/opt/trivy/bin",
        "/opt/cosign",
        "/opt/cosign/bin",
        "/opt/bin",
        "/root/.local/bin",
        "/root/bin",
        "/root/go/bin",
        "/snap/bin",
        "/var/lib/snapd/snap/bin",
        "/usr/libexec",
        "/usr/local/go/bin",
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/bin"),
        os.path.expanduser("~/go/bin"),
    ]
    for g in [
        "/home/*/.local/bin",
        "/home/*/bin",
        "/home/*/go/bin",
        "/opt/*/bin",
        "/var/lib/snapd/snap/bin",
        "/usr/local/*/bin",
    ]:
        common_dirs.extend(glob.glob(g))

    seen = set()
    for d in common_dirs:
        if d not in seen:
            seen.add(d)
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

    try:
        res = subprocess.run(
            ["whereis", "-b", name],
            capture_output=True, text=True, timeout=3
        )
        if res.returncode == 0:
            parts = res.stdout.strip().split()
            if len(parts) > 1:
                for p in parts[1:]:
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        return p
    except Exception:
        pass

    return None
