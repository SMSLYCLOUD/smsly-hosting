"""Bundle Provisioner.

Manages the lifecycle of custom addon bundles declared in ``grid.addons``
manifest files.  Provides feature parity with :class:`AddonProvisioner` for
standard addons — logs, status, health checks, backup/restore, rebuild,
and teardown.

Custom bundles are deployed as isolated Docker Compose stacks.  Each
bundle gets its own Docker network so that inter-service DNS works
automatically.  Services within a bundle can reference each other by
service name (the key in the ``services`` mapping).

For services declared with ``repo:`` (Git sources), images are built
through Grid's existing build pipeline (BuildKit / Nixpacks) and pushed
to the private registry before being used in the compose stack.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from typing import Any

from apps.addons.services.grid_addons_parser import (
    BundleDecl,
    BundleServiceDecl,
    resolve_bundle_env,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUNDLE_NETWORK_PREFIX = "smsly-bundle"
BUNDLE_LABEL_PREFIX = "grid.addon.bundle"
BUNDLE_COMPONENT_LABEL = "grid.addon.component"
BUNDLE_SERVICE_LABEL = "grid.addon.service"
BUNDLE_MANAGED_LABEL = "grid.addon.managed"
COMPOSE_FILE_NAME = "grid-addons-compose.yml"

# Strict allowlist of safe extra keys a bundle service may supply.
_ALLOWED_BUNDLE_EXTRA_KEYS = frozenset({
    "command", "entrypoint", "working_dir", "user",
    "healthcheck", "depends_on", "expose", "tmpfs", "ulimits",
})
_DANGEROUS_BUNDLE_EXTRA_KEYS = frozenset({
    "privileged", "network_mode", "pid", "ipc", "uts", "userns_mode",
    "cgroup", "cgroup_parent", "devices", "runtime", "cap_add", "cap_drop",
    "security_opt", "ports", "volumes",
})
_NAMED_BUNDLE_VOLUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FORBIDDEN_BUNDLE_LABEL_PREFIXES = ("traefik.", "com.paas.", "smsly.", "grid.addon.")

_DANGEROUS_BUNDLE_CAPS = frozenset({
    "ALL", "SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYS_MODULE",
    "SYS_RAWIO", "SYS_BOOT", "MAC_ADMIN", "MAC_OVERRIDE",
    "DAC_READ_SEARCH", "BPF", "PERFMON", "SYS_TIME",
    "AUDIT_CONTROL", "SYSLOG",
})
_FORBIDDEN_BUNDLE_BIND_PREFIXES = (
    "/etc", "/proc", "/sys", "/dev", "/var/run", "/run",
    "/var/lib/docker", "/root", "/boot", "/opt/smsly-hosting",
)


class BundleProvisioner:
    """Manages custom addon bundles defined in ``grid.addons``.

    Lifecycle methods mirror :class:`AddonProvisioner` so that bundle
    components appear and behave like standard addons in the dashboard.
    """

    _SCRIPT_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.-]+$")

    @classmethod
    def _validate_script_path(cls, script_path: Any) -> str:
        raw = str(script_path or "").strip()
        if not raw or ".." in raw or not cls._SCRIPT_PATH_RE.fullmatch(raw):
            raise ValueError(
                f"Unsafe bundle script path {script_path!r}: must match "
                "^/[A-Za-z0-9/_.-]+$ without '..'."
            )
        return raw

    # -------------------------------------------------------------------
    # Provision
    # -------------------------------------------------------------------

    def provision(
        self,
        bundle: BundleDecl,
        service_id: str,
        service_name: str,
        addon_urls: dict[str, str] | None = None,
        build_dir: str | None = None,
    ) -> dict[str, str]:
        """Spin up a custom bundle as a Docker Compose stack.

        Args:
            bundle: Parsed bundle declaration from ``grid.addons``.
            service_id: UUID of the parent Grid service.
            service_name: Human-readable service name (used in labels).
            addon_urls: Mapping of standard-addon names → connection URLs,
                used to resolve ``{{addons.postgres.url}}`` templates.
            build_dir: Working directory for building repo-based services.
                When ``None``, a temporary directory is created.

        Returns:
            Mapping of component name → connection URL (host:port) for each
            service in the bundle.
        """
        addon_urls = addon_urls or {}
        component_urls: dict[str, str] = {}
        temp_dir: str | None = None

        try:
            # Resolve template variables in env vars
            resolved_services = resolve_bundle_env(bundle.services, addon_urls)

            # Build repo-based services first
            built_images: dict[str, str] = {}
            for svc in resolved_services:
                if svc.source_type == "repo" and svc.repo:
                    image_tag, built_dir = self._build_repo_service(
                        svc, service_id, build_dir,
                    )
                    built_images[svc.name] = image_tag
                    if built_dir and not build_dir:
                        temp_dir = built_dir

            # Generate docker-compose.yml
            compose_path = self._generate_compose(
                bundle=bundle,
                services=resolved_services,
                service_id=service_id,
                service_name=service_name,
                built_images=built_images,
            )

            # Create isolated network (before compose up, after file write)
            network_name = self._network_name(bundle, service_id)
            self._ensure_network(network_name)

            # Start the stack
            self._compose_up(
                compose_path, network_name,
                bundle_name=bundle.name, service_id=service_id,
            )

            # Extract connection info for each service
            for svc in resolved_services:
                url = self._extract_service_url(svc, service_id)
                if url:
                    component_urls[svc.name] = url

            logger.info(
                "Bundle '%s' provisioned for service %s (%d components)",
                bundle.name, service_name, len(resolved_services),
            )
            return component_urls

        except Exception:
            # Rollback: best-effort cleanup on failure
            logger.warning(
                "Bundle '%s' provision failed, cleaning up", bundle.name,
            )
            self.deprovision(bundle.name, service_id)
            raise

        finally:
            # Clean up temp directory if we created one
            if temp_dir and os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------
    # Logs
    # -------------------------------------------------------------------

    def get_logs(
        self,
        bundle_name: str,
        service_id: str,
        component_name: str | None = None,
        tail: int = 200,
        follow: bool = False,
    ) -> str | subprocess.Popen:
        """Fetch logs from a bundle component.

        Args:
            bundle_name: Name of the bundle (from ``grid.addons``).
            service_id: UUID of the parent Grid service.
            component_name: Specific service within the bundle, or ``None``
                for all services in the bundle.
            tail: Max lines to return.
            follow: If ``True``, return a streaming ``Popen`` process.
                **Caller is responsible for killing and waiting on the
                returned process to avoid zombie processes.**

        Returns:
            Log text string, or a ``Popen`` process if streaming.
        """
        compose_path = self._compose_path(bundle_name, service_id)
        if not os.path.isfile(compose_path):
            return ""

        cmd = self._compose_base(bundle_name, service_id) + [
            "logs", "--tail", str(min(tail, 2000)), "--timestamps",
        ]
        if follow:
            cmd.append("-f")
        if component_name:
            cmd.append(component_name)

        if follow:
            return subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
            return result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            logger.warning("Timeout fetching logs for bundle %s", bundle_name)
            return ""
        except Exception as exc:
            logger.error("Failed to fetch logs for bundle %s: %s", bundle_name, exc)
            return ""

    # -------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------

    def get_status(
        self, bundle_name: str, service_id: str,
    ) -> dict[str, Any]:
        """Get status of all components in a bundle.

        Returns:
            Dict with ``running`` (bool), ``components`` (list of dicts
            with name, status, health).
        """
        compose_path = self._compose_path(bundle_name, service_id)
        if not os.path.isfile(compose_path):
            return {"running": False, "components": []}

        try:
            result = subprocess.run(
                self._compose_base(bundle_name, service_id) + ["ps", "--format", "json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return {
                    "running": False, "components": [],
                    "error": result.stderr.strip() or f"exit {result.returncode}",
                }
            components = []
            any_running = False
            for line in result.stdout.strip().splitlines():
                if not line:
                    continue
                try:
                    info = json.loads(line)
                except json.JSONDecodeError:
                    continue
                running = info.get("State", "").lower() == "running"
                if running:
                    any_running = True
                components.append({
                    "name": info.get("Name", ""),
                    "status": info.get("State", "unknown"),
                    "health": info.get("Health", "unknown"),
                    "ports": info.get("Publishers", []),
                })
            return {"running": any_running, "components": components}
        except Exception as exc:
            logger.error("Failed to get status for bundle %s: %s", bundle_name, exc)
            return {"running": False, "components": [], "error": str(exc)}

    def get_component_status(
        self, bundle_name: str, service_id: str, component_name: str,
    ) -> dict[str, Any]:
        """Get detailed status of a single component via ``docker inspect``."""
        container_name = self._container_name(bundle_name, service_id, component_name)
        try:
            result = subprocess.run(
                ["docker", "inspect", container_name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return {"running": False, "status": "not_found"}
            info = json.loads(result.stdout)[0]
            return {
                "running": info["State"]["Running"],
                "status": info["State"]["Status"],
                "started_at": info["State"].get("StartedAt"),
                "health": info["State"].get("Health", {}).get("Status", "unknown"),
            }
        except Exception as exc:
            logger.error("Failed to inspect component %s: %s", component_name, exc)
            return {"running": False, "status": "unknown", "error": str(exc)}

    # -------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------

    def health_check(
        self, bundle_name: str, service_id: str, component_name: str,
    ) -> dict[str, Any]:
        """Check health of a specific bundle component.

        Uses ``docker inspect`` to read the container's Health status.
        Returns ``{"status": "healthy"|"unhealthy"|"starting"|"unknown"}``.
        """
        status = self.get_component_status(bundle_name, service_id, component_name)
        health = status.get("health", "unknown")
        return {
            "status": health,
            "container_running": status.get("running", False),
            "details": status,
        }

    # -------------------------------------------------------------------
    # Reprovision (rebuild & restart)
    # -------------------------------------------------------------------

    def reprovision(
        self,
        bundle: BundleDecl,
        service_id: str,
        service_name: str,
        addon_urls: dict[str, str] | None = None,
        build_dir: str | None = None,
    ) -> dict[str, str]:
        """Tear down and re-provision a bundle.

        Rebuilds repo-based images and recreates all containers.
        """
        # Compute the actual network name before deprovisioning
        # (since deprovision needs it to remove the correct network)
        net = self._network_name(bundle, service_id)
        self.deprovision(bundle.name, service_id, network_name=net)
        return self.provision(
            bundle, service_id, service_name, addon_urls, build_dir,
        )

    # -------------------------------------------------------------------
    # Backup
    # -------------------------------------------------------------------

    def backup(
        self,
        bundle_name: str,
        service_id: str,
        component_name: str,
        backup_config: dict[str, Any] | None = None,
    ) -> str:
        """Create a backup for a bundle component.

        Uses the ``backup_script`` from ``grid.addons`` if provided,
        otherwise falls back to ``docker exec tar`` for volume data.

        Returns:
            Path to the backup file.
        """
        from datetime import datetime

        container_name = self._container_name(bundle_name, service_id, component_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join("/app", "backups", "bundles", service_id)
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(
            backup_dir, f"{bundle_name}_{component_name}_{timestamp}.tar.gz",
        )

        # Try user-provided backup script first (runs inside container, never on host)
        if backup_config and backup_config.get("backup_script"):
            script_path = self._validate_script_path(backup_config["backup_script"])
            try:
                result = subprocess.run(
                    ["docker", "exec", container_name, "sh", script_path],
                    capture_output=True, timeout=300,
                )
                if result.returncode == 0:
                    if result.stdout:
                        with open(backup_path, "wb") as fh:
                            fh.write(result.stdout)
                    if os.path.isfile(backup_path):
                        return backup_path
                logger.warning(
                    "Backup script failed for %s/%s: %s",
                    bundle_name, component_name,
                    (result.stderr or b"").decode(errors="replace"),
                )
            except Exception as exc:
                logger.warning(
                    "Backup script error for %s/%s: %s",
                    bundle_name, component_name, exc,
                )

        # Fallback: docker exec tar (safe format string)
        try:
            # Get mounted volumes — use Name only for named volumes,
            # Source for bind mounts.  The Go template handles both.
            result = subprocess.run(
                ["docker", "inspect", "--format",
                 '{{range .Mounts}}{{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}}:{{.Destination}};{{end}}',
                 container_name],
                capture_output=True, text=True, timeout=10,
            )
            mounts = result.stdout.strip()
            if mounts:
                paths = []
                for entry in mounts.split(";"):
                    if not entry or ":" not in entry:
                        continue
                    # Take everything after the first colon (the destination path)
                    dest = entry.split(":", 1)[1]
                    if dest:
                        paths.append(dest)

                if paths:
                    with open(backup_path, "wb") as fh:
                        tar_result = subprocess.run(
                            ["docker", "exec", container_name, "tar", "czf", "-", *paths],
                            stdout=fh, timeout=300,
                        )
                    if tar_result.returncode != 0:
                        logger.warning(
                            "tar failed for %s/%s (exit %d)",
                            bundle_name, component_name, tar_result.returncode,
                        )
                    elif os.path.isfile(backup_path) and os.path.getsize(backup_path) > 0:
                        return backup_path

            # Last resort: empty backup marker (not .tar.gz to avoid confusion)
            marker_path = backup_path.replace(".tar.gz", ".empty")
            with open(marker_path, "w") as fh:
                fh.write(f"Empty backup marker for {bundle_name}/{component_name}\n")
            return marker_path

        except Exception as exc:
            logger.error("Backup failed for %s/%s: %s", bundle_name, component_name, exc)
            raise

    # -------------------------------------------------------------------
    # Restore
    # -------------------------------------------------------------------

    def restore(
        self,
        bundle_name: str,
        service_id: str,
        component_name: str,
        backup_path: str,
        backup_config: dict[str, Any] | None = None,
    ) -> bool:
        """Restore a backup to a bundle component."""
        container_name = self._container_name(bundle_name, service_id, component_name)

        # Try user-provided restore script first (runs inside container, never on host)
        if backup_config and backup_config.get("restore_script"):
            script_path = self._validate_script_path(backup_config["restore_script"])
            try:
                result = subprocess.run(
                    ["docker", "exec", container_name, "sh", script_path],
                    capture_output=True, text=True, timeout=300,
                )
                if result.returncode == 0:
                    return True
                logger.warning(
                    "Restore script failed for %s/%s: %s",
                    bundle_name, component_name, result.stderr,
                )
            except Exception as exc:
                logger.warning(
                    "Restore script error for %s/%s: %s",
                    bundle_name, component_name, exc,
                )

        # Fallback: extract archive via a short-lived alpine helper container
        # while the target container is stopped (never `docker exec` on a stopped container).
        try:
            real_backup = os.path.realpath(backup_path)
            if not os.path.isfile(real_backup):
                logger.error("Restore archive not found: %s", backup_path)
                return False

            import tarfile
            with tarfile.open(real_backup, "r:gz") as tf:
                for member in tf.getmembers():
                    mname = member.name.lstrip("/")
                    if ".." in mname.split("/"):
                        logger.error("Unsafe tar member in bundle backup: %s", member.name)
                        return False
                    if member.issym() or member.islnk():
                        target = member.linkname or ""
                        if target.startswith("/") or ".." in target.split("/"):
                            logger.error("Unsafe link in bundle backup: %s -> %s", member.name, target)
                            return False

            result = subprocess.run(
                ["docker", "stop", container_name],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                logger.error("docker stop failed for %s: %s", container_name, result.stderr)

            backup_dir = os.path.dirname(real_backup)
            backup_file = os.path.basename(real_backup)
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--volumes-from", container_name,
                    "-v", f"{backup_dir}:/backup:ro",
                    "alpine:latest",
                    "tar", "xzf", f"/backup/{backup_file}", "-C", "/",
                ],
                capture_output=True, timeout=300,
            )
            if result.returncode != 0:
                logger.error("tar restore failed for %s: %s", container_name, result.stderr)
                subprocess.run(
                    ["docker", "start", container_name],
                    capture_output=True, timeout=30,
                )
                return False

            result = subprocess.run(
                ["docker", "start", container_name],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                logger.error("docker start failed for %s: %s", container_name, result.stderr)
                return False

            return True
        except Exception as exc:
            logger.error("Restore failed for %s/%s: %s", bundle_name, component_name, exc)
            return False

    # -------------------------------------------------------------------
    # Deprovision
    # -------------------------------------------------------------------

    def deprovision(
        self,
        bundle_name: str,
        service_id: str,
        network_name: str | None = None,
    ) -> bool:
        """Tear down a bundle: stop containers, remove network, clean up compose file.

        Args:
            bundle_name: Name of the bundle.
            service_id: UUID of the parent Grid service.
            network_name: Actual Docker network name.  When ``None``,
                computed from ``bundle_name`` (may differ if the bundle
                declared a custom ``network:`` in grid.addons).
        """
        compose_path = self._compose_path(bundle_name, service_id)
        try:
            if os.path.isfile(compose_path):
                subprocess.run(
                    self._compose_base(bundle_name, service_id) + ["down", "-v"],
                    capture_output=True, timeout=120,
                )
                os.remove(compose_path)

            # Remove network — use the provided name or compute from bundle_name
            net = network_name or self._network_name_raw(bundle_name, service_id)
            subprocess.run(
                ["docker", "network", "rm", net],
                capture_output=True, timeout=10,
            )

            logger.info("Deprovisioned bundle %s for service %s", bundle_name, service_id)
            return True
        except Exception as exc:
            logger.error("Failed to deprovision bundle %s: %s", bundle_name, exc)
            return False

    # -------------------------------------------------------------------
    # Metrics (Prometheus labels)
    # -------------------------------------------------------------------

    def get_metrics(
        self, bundle_name: str, service_id: str,
    ) -> dict[str, Any]:
        """Collect container metrics for all components in a bundle.

        Uses ``docker stats`` as a fallback when Prometheus is not available.
        Returns per-component CPU%, memory usage, network I/O.
        """
        compose_path = self._compose_path(bundle_name, service_id)
        if not os.path.isfile(compose_path):
            return {"components": []}

        try:
            result = subprocess.run(
                self._compose_base(bundle_name, service_id) + ["ps", "-q"],
                capture_output=True, text=True, timeout=10,
            )
            container_ids = result.stdout.strip().split()
            if not container_ids or container_ids == ['']:
                return {"components": []}

            stats_result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}",' '"net":"{{.NetIO}}","disk":"{{.BlockIO}}"}', *container_ids],
                capture_output=True, text=True, timeout=15,
            )
            components = []
            for line in stats_result.stdout.strip().splitlines():
                if not line:
                    continue
                try:
                    components.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed stats line: %s", line[:100])
            return {"components": components}
        except Exception as exc:
            logger.error("Failed to get metrics for bundle %s: %s", bundle_name, exc)
            return {"components": [], "error": str(exc)}

    # -------------------------------------------------------------------
    # Network check / repair
    # -------------------------------------------------------------------

    def ensure_network(
        self,
        bundle_name: str,
        service_id: str,
        network_name: str | None = None,
    ) -> str:
        """Verify the bundle network exists, create if missing.

        Args:
            bundle_name: Bundle name (used to compute network name if
                ``network_name`` is not provided).
            service_id: UUID of the parent Grid service.
            network_name: Explicit network name.  When ``None``,
                computed from ``bundle_name``.

        Returns the network name.
        """
        net = network_name or self._network_name_raw(bundle_name, service_id)
        self._ensure_network(net)
        return net

    # ===================================================================
    # Private helpers
    # ===================================================================

    def _network_name(self, bundle: BundleDecl, service_id: str) -> str:
        """Deterministic Docker network name for a bundle."""
        if bundle.network:
            return f"{BUNDLE_NETWORK_PREFIX}-{bundle.network}-{service_id[:8]}"
        return f"{BUNDLE_NETWORK_PREFIX}-{bundle.name}-{service_id[:8]}"

    def _network_name_raw(self, bundle_name: str, service_id: str) -> str:
        """Network name from bundle name only (no BundleDecl available)."""
        return f"{BUNDLE_NETWORK_PREFIX}-{bundle_name}-{service_id[:8]}"

    def _compose_path(self, bundle_name: str, service_id: str) -> str:
        """Path to the generated docker-compose file."""
        b_name = str(bundle_name or "")
        s_id = str(service_id or "")
        if (
            not b_name
            or not s_id
            or any(bad in val for val in (b_name, s_id) for bad in ("/", "\\", "..", "\x00"))
        ):
            raise ValueError("Invalid bundle_name or service_id for compose path.")
        bundles_root = os.path.realpath(os.path.join("/app", "bundles"))
        candidate = os.path.realpath(
            os.path.join(bundles_root, s_id, b_name, COMPOSE_FILE_NAME)
        )
        try:
            if os.path.commonpath([candidate, bundles_root]) != bundles_root or candidate == bundles_root:
                raise ValueError("Bundle compose path escapes /app/bundles.")
        except ValueError as exc:
            raise ValueError("Bundle compose path escapes /app/bundles.") from exc
        return candidate

    def _project_name(self, bundle_name: str, service_id: str) -> str:
        """Isolated compose project name for a bundle stack.

        AGENTS.md #16: never rely on directory-derived project names with
        ``--remove-orphans`` — two services deploying the same bundle name
        would share a project and orphan-delete each other's containers.
        """
        safe = re.sub(r'[^a-z0-9-]', '-', bundle_name.lower()).strip('-')
        return f"{BUNDLE_NETWORK_PREFIX}-{safe}-{service_id[:8]}"

    def _compose_base(
        self, bundle_name: str, service_id: str,
    ) -> list[str]:
        """Base ``docker compose`` argv with explicit isolated project."""
        return [
            "docker", "compose",
            "-p", self._project_name(bundle_name, service_id),
            "-f", self._compose_path(bundle_name, service_id),
        ]

    def _container_name(
        self, bundle_name: str, service_id: str, component_name: str,
    ) -> str:
        # Sanitize for Docker naming rules (lowercase, alphanumeric + dash)
        safe_component = re.sub(r'[^a-z0-9-]', '-', component_name.lower()).strip('-')
        safe_bundle = re.sub(r'[^a-z0-9-]', '-', bundle_name.lower()).strip('-')
        return f"smsly-bundle-{safe_bundle}-{safe_component}-{service_id[:8]}"

    def _ensure_network(self, network_name: str) -> None:
        """Create the Docker network if it doesn't exist."""
        result = subprocess.run(
            ["docker", "network", "inspect", network_name],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            subprocess.run(
                ["docker", "network", "create",
                 "--driver", "bridge",
                 "--label", f"{BUNDLE_MANAGED_LABEL}=true",
                 network_name],
                capture_output=True, timeout=10,
            )

    def _generate_compose(
        self,
        bundle: BundleDecl,
        services: list[BundleServiceDecl],
        service_id: str,
        service_name: str,
        built_images: dict[str, str],
    ) -> str:
        """Generate a docker-compose.yml for the bundle and write it to disk.

        Returns the path to the generated file.
        """
        compose_path = self._compose_path(bundle.name, service_id)
        os.makedirs(os.path.dirname(compose_path), exist_ok=True)

        network_name = self._network_name(bundle, service_id)
        network_alias = f"{bundle.name}-net"

        compose: dict[str, Any] = {
            "services": {},
            "networks": {
                network_alias: {
                    "external": True,
                    "name": network_name,
                },
            },
        }

        from apps.deployments.services.container_runtime import (
            detect_best_runtime,
            ensure_sandbox_resolv_conf,
            is_sandboxed_runtime,
            sandbox_dns_servers,
        )
        bundle_runtime = detect_best_runtime()
        bundle_dns: list[str] | None = None
        bundle_resolv_path: str | None = None
        if bundle_runtime and is_sandboxed_runtime(bundle_runtime):
            try:
                bundle_dns = sandbox_dns_servers()
            except Exception:
                bundle_dns = ["8.8.8.8", "1.1.1.1"]
            try:
                bundle_resolv_path = ensure_sandbox_resolv_conf(bundle_dns)
            except Exception:
                bundle_resolv_path = None

        for svc in services:
            # Build labels: user labels first (excluding platform namespaces), then platform labels
            labels: dict[str, str] = {}
            if svc.labels:
                for lbl in svc.labels:
                    if ":" in lbl:
                        k, v = lbl.split(":", 1)
                        k_clean = k.strip()
                        v_clean = v.strip()
                    else:
                        k_clean = lbl.strip()
                        v_clean = ""
                    k_lower = k_clean.lower()
                    if k_lower == "managed_by" or k_lower.startswith(_FORBIDDEN_BUNDLE_LABEL_PREFIXES):
                        continue
                    labels[k_clean] = v_clean
            labels.update({
                BUNDLE_MANAGED_LABEL: "true",
                BUNDLE_LABEL_PREFIX: bundle.name,
                BUNDLE_COMPONENT_LABEL: svc.name,
                BUNDLE_SERVICE_LABEL: service_name,
                "smsly.service_id": service_id,
            })

            svc_def: dict[str, Any] = {
                "networks": [network_alias],
                "labels": labels,
                "init": True,
                "security_opt": [
                    "no-new-privileges:true",
                    "apparmor:docker-default",
                ],
                "cap_drop": ["ALL"],
                "cap_add": ["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"],
                "pids_limit": 1024,
                "ulimits": {
                    "nofile": {"soft": 1024, "hard": 2048},
                    "fsize": 512000000,
                },
            }
            if bundle_runtime and bundle_runtime != "runc":
                svc_def["runtime"] = bundle_runtime
            if bundle_dns:
                svc_def["dns"] = list(bundle_dns)

            # Image source
            if svc.name in built_images:
                svc_def["image"] = built_images[svc.name]
            elif svc.image:
                svc_def["image"] = svc.image
            else:
                # Repo service that wasn't built (shouldn't happen)
                continue

            # Strip host port mappings on tenant bundle services (internal expose only)
            if svc.ports:
                logger.warning(
                    "Stripping host port mappings %s on bundle service '%s' (internal expose only)",
                    svc.ports,
                    svc.name,
                )
                exposed_ports: list[str] = []
                for p in svc.ports:
                    cport = str(p).split(":")[-1].split("/")[0].strip()
                    if cport and cport not in exposed_ports:
                        exposed_ports.append(cport)
                if exposed_ports:
                    svc_def["expose"] = exposed_ports

            # Volumes (reject ALL host bind mounts; allow named Docker volumes only)
            vols_list: list[str] = []
            if svc.volumes:
                for vol in svc.volumes:
                    v_str = str(vol).strip()
                    if ":" in v_str:
                        src = v_str.split(":", 1)[0].strip().replace("\\", "/")
                        if (
                            not src
                            or src.startswith(("/", "./", "../", "~", "$"))
                            or "/" in src
                            or not _NAMED_BUNDLE_VOLUME_RE.match(src)
                        ):
                            raise ValueError(
                                f"Bundle service '{svc.name}' uses host bind mount '{v_str}' "
                                "which is forbidden; use named Docker volumes only."
                            )
                    vols_list.append(v_str)
            if bundle_resolv_path and os.path.isfile(bundle_resolv_path):
                bind_spec = f"{bundle_resolv_path}:/etc/resolv.conf:ro"
                if not any(":/etc/resolv.conf" in v for v in vols_list):
                    vols_list.append(bind_spec)
            if vols_list:
                svc_def["volumes"] = vols_list

            # Environment
            env = svc.env_vars
            if env:
                svc_def["environment"] = env

            # Healthcheck
            if svc.healthcheck:
                svc_def["healthcheck"] = svc.healthcheck

            # Capabilities (validate against dangerous caps)
            if svc.cap_add:
                validated_caps = []
                for cap in svc.cap_add:
                    cap_norm = str(cap).strip().upper()
                    if cap_norm.startswith("CAP_"):
                        cap_norm = cap_norm[4:]
                    if cap_norm in _DANGEROUS_BUNDLE_CAPS:
                        raise ValueError(
                            f"Bundle service '{svc.name}' requests forbidden capability '{cap}'."
                        )
                    validated_caps.append(str(cap).strip())
                svc_def["cap_add"] = validated_caps

            # Command
            if svc.command:
                svc_def["command"] = svc.command

            # Depends-on (startup ordering)
            if svc.depends_on:
                svc_def["depends_on"] = list(svc.depends_on)

            # Restart policy
            if svc.restart:
                svc_def["restart"] = svc.restart
            else:
                svc_def["restart"] = "unless-stopped"

            # Extra keys — reject dangerous security directives explicitly, allowlist safe keys
            for k, v in svc.extra.items():
                if k in _DANGEROUS_BUNDLE_EXTRA_KEYS:
                    raise ValueError(
                        f"Bundle service '{svc.name}' cannot set restricted directive '{k}'."
                    )
                if k not in _ALLOWED_BUNDLE_EXTRA_KEYS:
                    logger.warning(
                        "Ignoring disallowed extra key '%s' for bundle service '%s'",
                        k, svc.name,
                    )
                    continue
                svc_def[k] = v

            compose["services"][svc.name] = svc_def

        # Write compose file
        import yaml
        with open(compose_path, "w", encoding="utf-8") as fh:
            yaml.dump(compose, fh, default_flow_style=False, sort_keys=False)

        return compose_path

    def _compose_up(
        self, compose_path: str, network_name: str,
        bundle_name: str = "", service_id: str = "",
    ) -> None:
        """Run ``docker compose up -d`` and poll for container readiness.

        Project is always explicit (AGENTS.md #16) — never ``--remove-orphans``
        without ``-p``. Callers should pass bundle_name/service_id; when they
        are absent the project falls back to directory-derived naming.
        """
        if bundle_name and service_id:
            base = self._compose_base(bundle_name, service_id)
        else:
            base = ["docker", "compose", "-f", compose_path]
        result = subprocess.run(
            base + ["up", "-d"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker compose up failed (exit {result.returncode}): "
                f"{result.stderr}"
            )

        # Poll for container readiness instead of blind sleep
        self._wait_for_containers(
            compose_path, timeout=30,
            bundle_name=bundle_name, service_id=service_id,
        )

    def _wait_for_containers(
        self, compose_path: str, timeout: int = 30,
        bundle_name: str = "", service_id: str = "",
    ) -> None:
        """Poll containers until they report running or timeout expires."""
        if bundle_name and service_id:
            base = self._compose_base(bundle_name, service_id)
        else:
            base = ["docker", "compose", "-f", compose_path]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    base + ["ps", "-q"],
                    capture_output=True, text=True, timeout=5,
                )
                container_ids = [
                    cid for cid in result.stdout.strip().split() if cid
                ]
                if not container_ids:
                    break

                # Check if all containers are running
                all_running = True
                for cid in container_ids:
                    inspect = subprocess.run(
                        ["docker", "inspect", "--format",
                         "{{.State.Running}}", cid],
                        capture_output=True, text=True, timeout=5,
                    )
                    if inspect.stdout.strip() != "true":
                        all_running = False
                        break

                if all_running:
                    return
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("Bundle readiness check failed: %s", exc)
            time.sleep(1)

    def _build_repo_service(
        self,
        svc: BundleServiceDecl,
        service_id: str,
        build_dir: str | None = None,
    ) -> tuple[str, str | None]:
        """Clone, build, and push a repo-based service.

        Returns:
            Tuple of (registry image tag, temp_dir_if_created).  The
            caller is responsible for cleaning up temp_dir.
        """
        tag = f"registry:5000/smsly-bundle-{svc.name}-{service_id[:8]}:latest"

        created_temp = None
        work_dir = build_dir
        if not work_dir:
            work_dir = f"/tmp/smsly-bundle-build-{uuid.uuid4().hex[:8]}"
            created_temp = work_dir
        os.makedirs(work_dir, exist_ok=True)

        repo_dir = os.path.join(work_dir, svc.name)

        # Clone (enforce HTTPS-only public Git URLs)
        repo_raw = str(svc.repo or '').strip()
        if not repo_raw or repo_raw.startswith('-') or repo_raw.lower().startswith(('ext::', 'fd::')):
            raise ValueError("Invalid or unsafe bundle repository URL.")
        from urllib.parse import urlparse
        from apps.core.validators import validate_ssrf
        parsed_repo = urlparse(repo_raw)
        if parsed_repo.scheme.lower() != "https" or not parsed_repo.hostname:
            raise ValueError("Bundle repository URL must use https:// with a public hostname.")
        validate_ssrf(repo_raw, fail_closed_dns=True)

        clone_cmd = [
            "git", "clone",
            "-c", "http.followRedirects=false",
            "-c", "protocol.file.allow=never",
            "--depth", "1",
        ]
        if svc.branch:
            branch_str = str(svc.branch).strip()
            if branch_str.startswith('-') or any(c in branch_str for c in ('\x00', '\r', '\n')):
                raise ValueError("Invalid bundle branch name.")
            clone_cmd.extend(["--branch", branch_str])
        clone_cmd.extend(["--", repo_raw, repo_dir])

        result = subprocess.run(
            clone_cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr}")

        from apps.deployments.services.pipeline.clone import _sanitize_repo_symlinks
        _sanitize_repo_symlinks(repo_dir)

        # Build context (must stay inside repo_dir)
        repo_real = os.path.realpath(repo_dir)
        context = os.path.realpath(os.path.join(repo_real, svc.context or "."))
        if context != repo_real and not context.startswith(repo_real + os.sep):
            raise ValueError(f"Bundle service '{svc.name}' context escapes cloned repository.")

        # Detect build strategy
        build_type = svc.build or "dockerfile"
        dockerfile = svc.dockerfile

        if build_type == "dockerfile":
            if not dockerfile:
                # Auto-detect Dockerfile
                for candidate in ("Dockerfile", "dockerfile", "Containerfile"):
                    cand_path = os.path.join(context, candidate)
                    if os.path.isfile(cand_path) and not os.path.islink(cand_path):
                        dockerfile = candidate
                        break

            dockerfile_path = os.path.realpath(
                os.path.join(context, dockerfile) if dockerfile else os.path.join(context, "Dockerfile")
            )
            if not dockerfile_path.startswith(repo_real + os.sep):
                raise ValueError(f"Bundle service '{svc.name}' Dockerfile path escapes cloned repository.")

            build_cmd = [
                "docker", "build",
                "-t", tag,
                "-f", dockerfile_path,
                context,
            ]
        else:
            # Nixpacks build
            build_cmd = [
                "nixpacks", "build",
                "--name", tag,
                context,
            ]

        result = subprocess.run(
            build_cmd, capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Build failed for {svc.name} (exit {result.returncode}): "
                f"{result.stderr}"
            )

        # Push to registry — raise on failure so compose up doesn't fail
        # with a confusing "image not found" error
        result = subprocess.run(
            ["docker", "push", tag],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Docker push failed for {tag} (exit {result.returncode}): "
                f"{result.stderr}"
            )

        return tag, created_temp

    def _extract_service_url(
        self,
        svc: BundleServiceDecl,
        service_id: str,
    ) -> str | None:
        """Extract a connection URL for a bundle component.

        For services with ports, returns ``localhost:host_port`` (using
        the first mapped host port).  For internal-only services,
        returns the Docker DNS name on the bundle network.
        """
        if svc.ports:
            host_port = self._parse_host_port(svc.ports[0])
            if host_port:
                return f"localhost:{host_port}"

        # Fallback: Docker DNS name on the bundle network
        return svc.name

    @staticmethod
    def _parse_host_port(port_spec: str) -> str | None:
        """Extract the host port from a Docker port specification.

        Handles:
            "8080:80"           → "8080"
            "127.0.0.1:8080:80" → "8080"
            "80"                → None  (container-only, no host mapping)
            "8080-8090:8080-8090" → "8080"  (range — return start)
            "udp:5060:5060/udp" → "5060"
        """
        # Strip protocol prefix (e.g. "udp:5060:5060/udp")
        spec = port_spec
        if ":" in spec:
            prefix = spec.split(":")[0]
            if prefix.lower() in ("tcp", "udp"):
                spec = ":".join(spec.split(":")[1:])

        parts = spec.split(":")
        if len(parts) == 1:
            # Container-only: "80" — no host port mapped
            return None
        if len(parts) == 2:
            # host:container or host_ip:host:container — host is parts[0]
            return parts[0].split("-")[0]
        if len(parts) >= 3:
            # ip:host:container — host is parts[-2]
            return parts[-2].split("-")[0]

        return None


# Singleton
bundle_provisioner = BundleProvisioner()
