"""
Container runtime detection and selection for sandboxed isolation.

Supports:
  - gVisor (runsc) — user-space kernel, no KVM required, ~50MB overhead per container
  - Kata Containers (kata-runtime) — VM-level isolation, requires KVM
  - runc (default) — standard Docker runtime

Priority: kata > gVisor > runc
"""

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

RUNTIME_ENV_VAR = "SMSLY_CONTAINER_RUNTIME"
RUNTIME_OVERRIDE_ENV = "SMSLY_CONTAINER_RUNTIME_OVERRIDE"


def detect_best_runtime() -> str:
    """
    Detect the best available sandboxed runtime.

    Returns one of: 'kata-runtime', 'runsc', 'runc'
    """
    override = os.environ.get(RUNTIME_OVERRIDE_ENV, "").strip().lower()
    if override:
        if override in ("runc", "default", "none", "false", "0", "no"):
            return "runc"
        if override in ("kata", "kata-runtime"):
            if _kata_available():
                return "kata-runtime"
        if override in ("runsc", "gvisor"):
            if _runsc_available():
                return "runsc"
        logger.warning("Override runtime %r not available, falling back to auto-detect", override)

    env_runtime = os.environ.get(RUNTIME_ENV_VAR, "").strip().lower()
    if env_runtime in ("kata", "kata-runtime") and _kata_available():
        return "kata-runtime"
    if env_runtime in ("runsc", "gvisor") and _runsc_available():
        return "runsc"

    if _kata_available():
        return "kata-runtime"
    if _runsc_available():
        return "runsc"
    return "runc"


def _kata_available() -> bool:
    """Check if kata-runtime is installed and available."""
    try:
        from apps.deployments.utils import find_binary
        docker_bin = find_binary("docker") or "docker"
        result = subprocess.run(
            [docker_bin, "info", "--format", "{{json .Runtimes}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and ("kata-runtime" in result.stdout or "kata" in result.stdout):
            return True
        kata_bin = find_binary("kata-runtime")
        if not kata_bin:
            return False
        if not os.path.exists("/dev/kvm"):
            return False
        result = subprocess.run(
            [kata_bin, "kata-check", "--verbose"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ImportError):
        return False


def _runsc_available() -> bool:
    """Check if gVisor (runsc) runtime is available."""
    try:
        from apps.deployments.utils import find_binary
        docker_bin = find_binary("docker") or "docker"
        result = subprocess.run(
            [docker_bin, "info", "--format", "{{json .Runtimes}}"],
            capture_output=True, text=True, timeout=10,
        )
        if "runsc" in result.stdout:
            return True
        if find_binary("runsc"):
            return True
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ImportError):
        return bool(shutil.which("runsc") or os.path.exists("/usr/local/bin/runsc") or os.path.exists("/usr/bin/runsc"))


def is_sandboxed_runtime(runtime: str | None) -> bool:
    """Whether the runtime provides VM-level isolation."""
    return runtime in ("kata-runtime", "runsc")


def get_runtime_for_container(
    service_name: str = "",
    runtime_preference: str | None = None,
) -> str | None:
    """
    Get the Docker runtime string for a container.

    Returns None for default (runc), otherwise the runtime name.
    """
    if runtime_preference:
        runtime_preference = runtime_preference.strip().lower()

    if runtime_preference in ("kata", "kata-runtime") and _kata_available():
        return "kata-runtime"
    if runtime_preference in ("runsc", "gvisor") and _runsc_available():
        return "runsc"

    runtime = detect_best_runtime()
    if runtime == "runc":
        return None
    return runtime


class ContainerRuntime:
    """Wrapper for common Docker container operations."""

    def __init__(self):
        import docker
        self.client = docker.from_env()

    def restart_container(self, container_id: str, timeout: int = 10):
        """Restart a container by ID. Returns the container object."""
        container = self.client.containers.get(container_id)
        container.restart(timeout=timeout)
        return container

    def get_container(self, container_id: str):
        """Get a container object by ID."""
        return self.client.containers.get(container_id)
