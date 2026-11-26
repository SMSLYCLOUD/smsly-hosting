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


def sandbox_dns_servers() -> list[str]:
    """Explicit DNS resolvers for sandboxed (gVisor/Kata) containers.

    Sandboxed runtimes handle the loopback range inside the sandbox,
    so Docker's embedded DNS at 127.0.0.11 is unreachable from them
    (zero packets ever leave the sandbox for it) — every hostname
    lookup fails, including addon and internet names. The platform
    works around addon names via /etc/hosts injection, but general
    DNS needs real resolvers passed as container ``dns=``.

    Order: master CoreDNS mesh IP first (serves mesh.internal names
    AND forwards upstream), then public fallbacks. Env override
    SMSLY_RUNSC_DNS (comma-separated) wins when set.
    """
    override = os.environ.get("SMSLY_RUNSC_DNS", "").strip()
    if override:
        return [part.strip() for part in override.split(",") if part.strip()]

    servers: list[str] = []
    try:
        from apps.deployments.models.mesh import MeshNetwork

        mesh = MeshNetwork.objects.filter(name="default", is_active=True).first()
        if mesh is not None:
            local_peer = (
                mesh.peers.filter(is_local=True, is_active=True).first()
            )
            if local_peer is not None and local_peer.wg_address:
                servers.append(str(local_peer.wg_address))
    except Exception as exc:
        logger.debug("sandbox DNS: mesh IP lookup failed: %s", exc)
    servers.extend(["8.8.8.8", "1.1.1.1"])
    return servers


SANDBOX_RESOLV_CONF_FILENAME = "runsc-resolv.conf"


def ensure_sandbox_resolv_conf(dns_servers: list[str] | None = None) -> str | None:
    """Materialize a host-persisted resolv.conf for sandboxed (runsc/Kata) containers.

    On user-defined Docker bridges, Docker Engine unconditionally writes
    ``nameserver 127.0.0.11`` into ``/etc/resolv.conf`` even when ``dns=``
    is passed in HostConfig (using ``dns=`` only as upstream forwarders for
    its embedded resolver). Because gVisor's userspace netstack traps
    127.0.0.0/8 inside the sandbox, 127.0.0.11 is unreachable and external
    DNS lookups fail unless ``/etc/resolv.conf`` points directly at real
    resolvers.

    Obeys AGENTS.md #24:
    - Writes into ``SMSLY_BUILDS_DIR`` (default ``/opt/smsly-hosting/builds``),
      which is bind-mounted at the identical path on host, backend, and celery.
    - Self-heals if the target path was accidentally poisoned as a directory.
    - Never deletes the file once written (containers with ``unless-stopped``
      reference it across daemon/host restarts).
    - Returns the absolute file path ONLY when ``os.path.isfile(path)`` is
      True; returns None when running without the shared builds directory.
    """
    raw_servers = dns_servers if dns_servers is not None else sandbox_dns_servers()
    servers = [
        str(s).strip()
        for s in (raw_servers or [])
        if s and str(s).strip() and str(s).strip() != "127.0.0.11"
    ]
    if not servers:
        servers = ["8.8.8.8", "1.1.1.1"]

    builds_dir = (os.environ.get("SMSLY_BUILDS_DIR") or "/opt/smsly-hosting/builds").strip()
    if not builds_dir or not os.path.isdir(builds_dir):
        return None

    target_path = os.path.join(builds_dir, SANDBOX_RESOLV_CONF_FILENAME)
    try:
        if os.path.isdir(target_path) and not os.path.islink(target_path):
            shutil.rmtree(target_path)
        lines = [
            "# Managed by smsly-hosting for sandboxed (runsc/Kata) containers",
            *[f"nameserver {srv}" for srv in servers],
            "options ndots:0 timeout:2 attempts:2",
            "",
        ]
        desired = "\n".join(lines)
        current = None
        if os.path.isfile(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as fh:
                    current = fh.read()
            except OSError:
                current = None
        if current != desired:
            tmp_path = f"{target_path}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(desired)
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, target_path)
        if os.path.isfile(target_path):
            return target_path
    except OSError as exc:
        logger.warning("Failed to materialize sandbox resolv.conf at %s: %s", target_path, exc)
    return None



def _service_has_active_sidecar(service_name: str) -> bool:
    """Return True if *service_name* has an active mTLS/Envoy sidecar."""
    if not service_name:
        return False
    try:
        from apps.mtls.models import EnvoySidecar
        return EnvoySidecar.objects.filter(
            service__name=service_name,
            status__in=("RUNNING", "ACTIVE", "HEALTHY", "PROVISIONING"),
        ).exists()
    except Exception:
        return False


def _service_has_mtls_enabled(service_name: str) -> bool:
    """Return True if *service_name* opts into mTLS/mesh sidecar networking."""
    if not service_name:
        return False
    try:
        from apps.mtls.models import MtlsConfig
        if MtlsConfig.objects.filter(service__name=service_name, enabled=True).exists():
            return True
    except Exception:
        pass
    try:
        from apps.deployments.models import Service
        return Service.objects.filter(name=service_name, use_internal_network=True).exists()
    except Exception:
        return False


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
        if runtime_preference in ("runc", "default", "none", "false", "0", "no"):
            return None

    if runtime_preference in ("kata", "kata-runtime") and _kata_available():
        return "kata-runtime"
    if runtime_preference in ("runsc", "gvisor") and _runsc_available():
        return "runsc"

    # gVisor (runsc) traps local loopback/AF_UNIX traffic needed by Envoy/SPIRE
    # mTLS sidecars; fall back to default runc (None) when active sidecar or
    # mTLS is enabled on the service.
    if _service_has_active_sidecar(service_name) or _service_has_mtls_enabled(service_name):
        return None

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
        try:
            from apps.deployments.services.mesh_dns import sync_dns_now
            sync_dns_now(client=self.client)
        except Exception:
            pass
        return container

    def stop_container(self, container_id: str, timeout: int = 30):
        """Stop a container by ID (no-op when already stopped)."""
        container = self.client.containers.get(container_id)
        container.reload()
        if str((container.attrs.get('State', {}) or {}).get('Status') or '').lower() not in ('exited', 'dead', 'removing'):
            container.stop(timeout=timeout)
            try:
                from apps.deployments.services.mesh_dns import sync_dns_now
                sync_dns_now(client=self.client)
            except Exception:
                pass
        return container

    def start_container(self, container_id: str):
        """Start a container by ID. Returns the container object."""
        container = self.client.containers.get(container_id)
        container.reload()
        if str((container.attrs.get('State', {}) or {}).get('Status') or '').lower() != 'running':
            container.start()
            try:
                from apps.deployments.services.mesh_dns import sync_dns_now
                sync_dns_now(client=self.client)
            except Exception:
                pass
        return container

    def get_container(self, container_id: str):
        """Get a container object by ID."""
        return self.client.containers.get(container_id)
