import json
import logging
import os
import re
import shutil
import subprocess

import yaml

from django.conf import settings
from django.utils import timezone
from apps.deployments.services.builders import cleanup_stuck_buildkit as _cleanup_stuck_buildkit
from apps.deployments.services.builders import is_buildkit_cache_error, prune_buildkit_cache
from apps.deployments.services.builders import _platform_build_limits as _build_limits

from apps.cloud.services.builder import NixpacksBuilder
from apps.deployments.models import PlatformConfig
from apps.deployments.utils import (
    append_log,
    extract_dockerfile_arg_names,
    log_exhaustive_build_diagnostics,
    redact_values,
    update_stage,
)
from .exceptions import BuildError
from .utils import _read_env_file


logger = logging.getLogger(__name__)

# Bounds for the failure-tail drain (no bare magic numbers in the new
# failure path). The SDK stream can be megabytes on big builds; only the
# tail ever explains a failure.
BUILD_FAIL_TAIL_LINES = 60
BUILD_FAIL_TAIL_CHARS = 8000

# ── Build engine selection ──────────────────────────────────────────
# SMSLY_DOCKER_BUILDER=classic forces the legacy docker-py path.
# Default (anything else) builds with daemon-embedded BuildKit via the
# plain `docker` CLI (`DOCKER_BUILDKIT=1 docker build`) — deliberately
# NOT `docker buildx build`: buildx builder selection is per-user client
# state that rotted silently (2026-09-24), while the plain CLI has no
# selection to rot. Falls back to classic only on infrastructure-class
# failures (missing CLI, daemon unreachable, no builder).
_BUILDX_INFRA_RE = re.compile(
    r"no such file or directory.*\bdocker\b"
    r"|\bdocker\b.*command not found|command not found.*\bdocker\b"
    r"|errno 2.*\bdocker\b"
    r"|cannot connect to the [Dd]ocker daemon"
    r"|error during connect"
    r"|connection refused"
    r"|permission denied.*(?:docker\.sock|connect)"
    r"|no builder|unknown driver|failed to dial|buildkitd"
    r"|SMSLY_INFRA_NO_BUILD_OUTPUT",
    re.IGNORECASE,
)

def _use_buildx() -> bool:
    """Build engine selector. Env SMSLY_DOCKER_BUILDER=classic opts out."""
    return os.environ.get("SMSLY_DOCKER_BUILDER", "buildx").strip().lower() != "classic"


def _ephemeral_builder_enabled() -> bool:
    """Ephemeral per-build docker-container builders (real containment).

    Off by default; opt in with SMSLY_BUILDER_EPHEMERAL=1. When on, each
    build gets a throwaway builder whose buildkitd container carries the
    UI-tuned memory/CPU caps — unlike the systemd-run scope, which only
    constrains the CLI wrapper on the docker driver. Registry cache
    (--cache-from) keeps throwaway builders warm.
    """
    return os.environ.get("SMSLY_BUILDER_EPHEMERAL", "").strip().lower() in (
        "1", "true", "yes")


def _create_ephemeral_builder(name: str, mem_mb: int, cpu_pct: int) -> str | None:
    """Create a docker-container builder with resource caps. Name or None.

    Scoped to smsly-net: the internal registry (registry:5000) only
    resolves via Docker embedded DNS on user-defined networks. On the
    default bridge it NXDOMAINs (2026-09-26 policy retry incident),
    forcing every build cold.
    """
    try:
        created = subprocess.run(
            ["docker", "buildx", "create", "--driver", "docker-container",
             "--driver-opt", "network=smsly-net",
             "--name", name],
            capture_output=True, text=True, timeout=60,
        )
        if created.returncode != 0:
            logger.warning("Ephemeral builder create failed: %s",
                           (created.stderr or "").strip()[:200])
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Ephemeral builder create failed: %s", exc)
        return None
    try:
        subprocess.run(
            ["docker", "update", "--memory", f"{mem_mb}m",
             "--cpus", f"{cpu_pct / 100:g}",
             f"buildx_buildkit_{name}0"],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("Ephemeral builder cap apply failed (build proceeds): %s", exc)
    return name


def _remove_ephemeral_builder(name: str) -> None:
    """Best-effort removal. Never raises."""
    try:
        subprocess.run(
            ["docker", "buildx", "rm", "-f", name],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass


def _is_buildx_infra_error(text: str) -> bool:
    """True when a buildx failure is environmental (fallback may help)."""
    return bool(_BUILDX_INFRA_RE.search(str(text or "")))


def _stage_secret_files(secrets: dict | None) -> tuple[list, list]:
    """Write {id: value} to chmod-600 tmpfiles. Returns (ids, paths).

    Caller MUST pass paths to _cleanup_secret_files in a finally block —
    the classic path once staged these and never deleted them, leaving
    token material in /tmp indefinitely.
    """
    import tempfile
    ids: list = []
    paths: list = []
    for secret_id, secret_val in (secrets or {}).items():
        if not secret_val:
            continue
        fd, p = tempfile.mkstemp(prefix=f"smsly-secret-{secret_id}-")
        try:
            os.write(fd, str(secret_val).encode())
        finally:
            os.close(fd)
        os.chmod(p, 0o600)
        ids.append(secret_id)
        paths.append(p)
    return ids, paths


def _cleanup_secret_files(paths) -> None:
    """Best-effort removal of staged secret files. Never raises."""
    for p in paths or []:
        try:
            os.remove(p)
        except OSError:
            pass


# ── Registry-qualified local image names (build-cache engagement) ──
# Bare `ns/name:tag` names make the daemon resolve docker.io, so
# `cache_from` stays empty (see _registry_cache_settings) and every
# build recompiles all layers once local cache is pruned. Prefixing
# with the daemon-local registry host (`registry:5000`) engages
# classic-builder layer caching + pull-on-miss + push-after-build, so
# code-only redeploys reuse dependency layers.
# Gates (evaluated in order):
#   * SMSLY_REGISTRY_IMAGE_NAMES=0/false/no/off restores legacy bare names;
#   * CONTAINER_REGISTRY_URL must be daemon-local (registry:/127./localhost
#     markers — never an external host like ECR);
#   * local-daemon builds take the local host; remote/agent builds take the
#     mesh host, but only after a live reachability probe (agents cannot
#     resolve `registry:5000`, and an unreachable mesh must keep legacy
#     bare names rather than fail the build).
def _local_registry_host() -> str:
    raw = (getattr(settings, "CONTAINER_REGISTRY_URL", "") or "").strip()
    host = raw.split("://")[-1].rstrip("/").split("/")[0]
    if host.startswith(("registry:", "127.0.0.1:", "localhost:")):
        return host
    return ""


def _with_local_registry(bare_name: str, deployment=None) -> str:
    """Prefix `ns/name:tag` with the local registry host when safe.

    Pure function except the deployment-locality check. Never
    double-prefixes; never touches already-qualified (external) names.
    """
    name = (bare_name or "").strip()
    if not name:
        return name
    if os.environ.get("SMSLY_REGISTRY_IMAGE_NAMES", "true").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return name
    first = name.split("/")[0] if "/" in name else ""
    if "." in first or ":" in first:
        return name  # already registry-qualified (or external) — keep
    host = _local_registry_host()
    if not host:
        return name
    if deployment is not None:
        try:
            from apps.deployments.utils import is_deployment_local
            if not is_deployment_local(deployment):
                # Agent/remote daemons can't resolve the local name — try
                # the mesh address (probe-gated, "" when unreachable).
                mesh = _mesh_registry_host()
                return f"{mesh}/{name}" if mesh else name
        except Exception:
            return name
    return f"{host}/{name}"


# Maximum seconds a mesh-registry reachability probe may take.
_MESH_PROBE_TIMEOUT = 3

# Cached per worker process once a mesh registry answers.
_MESH_OK_HOST: str | None = None


def _mesh_registry_host() -> str:
    """Registry host:port reachable from a REMOTE build daemon, or "".

    Remote/agent daemons cannot resolve `registry:5000`; they reach the
    master registry over WireGuard. Returns e.g. "10.100.0.1:5000" only
    after a live TCP probe — a cached success is reused, failures always
    re-probe (the mesh may come up later). Never raises.
    """
    global _MESH_OK_HOST
    if _MESH_OK_HOST:
        return _MESH_OK_HOST
    try:
        if os.environ.get("SMSLY_MESH_REGISTRY_CACHE", "true").strip().lower() in (
            "0", "false", "no", "off",
        ):
            return ""
        # Only meaningful when the platform itself runs the internal
        # registry (never rewrite third-party/ECR names to the mesh).
        if not _local_registry_host():
            return ""
        try:
            from apps.deployments.services.provisioner.helpers.server_config import (
                _get_master_mesh_ip,
            )
            mesh_ip = (_get_master_mesh_ip() or "").strip() or "10.100.0.1"
        except Exception:
            mesh_ip = "10.100.0.1"
        import socket
        with socket.create_connection((mesh_ip, 5000), timeout=_MESH_PROBE_TIMEOUT):
            pass
        _MESH_OK_HOST = f"{mesh_ip}:5000"
        return _MESH_OK_HOST
    except Exception as exc:
        logger.debug("Mesh registry unreachable, agent builds stay bare: %s", exc)
        return ""


def _registry_cache_settings(image_name, build_args):
    """Registry-backed BuildKit cache settings for an image build.

    Returns ``(cache_from, build_args)``. ``cache_from`` points at the
    previous image only when the name is registry-qualified (bare
    names make BuildKit resolve docker.io and fail with
    insufficient_scope). In that case ``BUILDKIT_INLINE_CACHE=1`` is
    also defaulted in so the pushed image carries layer metadata to
    rebuild from — without it ``cache_from`` is dead weight and every
    redeploy recompiles all layers once the local BuildKit cache is
    pruned. An explicit caller value always wins (setdefault).
    """
    name = image_name or ""
    registry_host = name.split("/")[0] if "/" in name else ""
    use_cache = bool(registry_host) and ("." in registry_host or ":" in registry_host)
    if not use_cache:
        return [], build_args
    merged = dict(build_args or {})
    merged.setdefault("BUILDKIT_INLINE_CACHE", "1")
    return [name], merged


class BuildMixin:
    def _build_image(self):
        """Step 2: Build Image (with cache check)."""
        update_stage(self.deployment, 'Build', 'running')
        start_time = timezone.now()
        self._check_cancellation('Build')

        # Pre-flight: check disk space before starting a build.
        # Low disk is the #1 cause of "error reading from server: EOF" during
        # the final image export phase.
        try:
            usage = shutil.disk_usage("/")
            free_gb = usage.free // (1024 ** 3)
            if free_gb < 5:
                raise BuildError(
                    f"Insufficient disk space for build: only {free_gb}GB free, "
                    f"need at least 5GB. Free up space (e.g. 'docker system prune -af' "
                    f"&& 'docker builder prune -af') and retry."
                )
            if free_gb < 10:
                append_log(
                    self.deployment,
                    f"⚠ Low disk space warning: {free_gb}GB free — "
                    f"builds may fail if cache is large.\n"
                )
        except BuildError:
            raise
        except OSError:
            pass  # disk check is best-effort

        try:
            # For DOCKER type services with a pre-built image, use it directly
            if self.service.deploy_type == 'DOCKER' and self.service.docker_image:
                self.image_name = self.service.docker_image
                append_log(
                    self.deployment,
                    f"✓ Using pre-built image: {self.image_name}\n"
                )
                update_stage(
                    self.deployment, 'Build', 'success',
                    (timezone.now() - start_time).total_seconds()
                )
                return

            if self.service.deploy_type == 'GIT' and not self._source_tree_available(self.source_dir):
                append_log(
                    self.deployment,
                    "Build source directory is missing. Re-cloning before build...\n",
                )
                self._clone_repo()

            tag_hash = self.deployment.commit_hash[:7]
            from apps.deployments.services.registry_credentials import (
                project_image_namespace,
            )
            _ns = project_image_namespace(self.service)
            self.image_name = _with_local_registry(
                f"{_ns}/{self.service.name.lower()}:{tag_hash}",
                self.deployment,
            )

            # ── Build cache: skip if image already exists locally ──
            if tag_hash != 'latest':
                try:
                    import docker as docker_lib
                    client = docker_lib.from_env()
                    client.images.get(self.image_name)
                    # Image exists — skip entire build
                    update_stage(
                        self.deployment, 'Build', 'success',
                        (timezone.now() - start_time).total_seconds()
                    )
                    append_log(
                        self.deployment,
                        f"✓ Build skipped — cached image found: {self.image_name}\n"
                    )
                    return
                except docker_lib.errors.ImageNotFound:
                    # Pull-on-miss: the tag may live in the local registry
                    # (retention keeps rollback images there) even when the
                    # daemon pruned it. A pull is seconds; a rebuild is
                    # minutes. Falls through to a full build on any failure.
                    if self._pull_cached_image():
                        update_stage(
                            self.deployment, 'Build', 'success',
                            (timezone.now() - start_time).total_seconds()
                        )
                        return
                    append_log(
                        self.deployment,
                        f"  Cache miss — image {self.image_name} not found locally, building...\n"
                    )
                except Exception as cache_err:
                    append_log(
                        self.deployment,
                        f"  Cache check error ({type(cache_err).__name__}), proceeding with build: {cache_err}\n"
                    )

            # Compose mode: build + start all compose services
            if self.service.deploy_mode == 'COMPOSE':
                self._build_with_compose()
                update_stage(
                    self.deployment, 'Build', 'success',
                    (timezone.now() - start_time).total_seconds()
                )
                append_log(
                    self.deployment,
                    f"✓ Compose build successful: {self.service.compose_file}\n"
                )
                return

            # Determine build context (root dir)
            context_dir = self._get_build_context()

            # Dockerfile detection
            dockerfile_path = self._find_dockerfile(context_dir)

            if self.service.buildpack == 'DOCKER':
                use_docker = True
            elif self.service.buildpack == 'NIXPACKS' or self.service.buildpack == 'STATIC':
                use_docker = False
            else:
                # AUTO or other
                use_docker = bool(dockerfile_path)

            builder_label = "docker" if use_docker else str(self.service.buildpack or "nixpacks").lower()
            log_exhaustive_build_diagnostics(self.deployment, builder_label, context_dir)

            if use_docker:
                if not dockerfile_path:
                    raise BuildError(
                        "Build strategy is docker but no Dockerfile was found. "
                        "Nixpacks fallback is disabled for Docker-selected services."
                    )
                append_log(self.deployment, "Build strategy: docker\n")
                self._build_with_docker(context_dir, dockerfile_path)
            else:
                if self.service.buildpack == 'NIXPACKS':
                    append_log(self.deployment, "Build strategy: nixpacks\n")
                elif self.service.buildpack == 'STATIC':
                    append_log(self.deployment, "Build strategy: static (via nixpacks)\n")
                else:
                    append_log(self.deployment, "Build strategy: nixpacks fallback\n")
                self._build_with_nixpacks(context_dir)

            update_stage(
                self.deployment, 'Build', 'success',
                (timezone.now() - start_time).total_seconds()
            )
            append_log(self.deployment, f"✓ Build successful: {self.image_name}\n")
            # Cache refresh: drop previous stale cache so the next build
            # starts from current layers (bounded + fresh). Best-effort.
            try:
                from apps.deployments.services.builders import prune_stale_build_cache
                prune_stale_build_cache()
            except Exception:
                logger.debug("Post-build cache refresh skipped", exc_info=True)

        except Exception as e:
            update_stage(self.deployment, 'Build', 'failed')
            # AUTO-RECOVERY: detect containerd/cache corruption and trigger
            # an automatic Docker state cleanup so the NEXT build succeeds
            # instead of hitting the same corrupted layer repeatedly.
            try:
                from apps.deployments.tasks.build_recovery import (
                    is_build_corruption_error,
                    recover_corrupt_docker_state,
                )
                if is_build_corruption_error(str(e)):
                    append_log(
                        self.deployment,
                        "⚠ Docker layer corruption detected — auto-recovery: "
                        "pruning build cache + restarting Docker daemon...\n",
                    )
                    recover_corrupt_docker_state.delay(deployment_id=str(self.deployment.id))
            except ImportError:
                pass  # recovery task not available — just fail normally
            except Exception as recovery_exc:
                logger.debug("Corruption recovery dispatch failed: %s", recovery_exc)
            raise BuildError(f"Build failed: {e!s}") from e


    def _build_with_compose(self):
        """Build and start all services via docker-compose."""
        compose_path = os.path.join(self.source_dir, self.service.compose_file)
        if not os.path.isfile(compose_path):
            raise BuildError(
                f"Compose file not found: {self.service.compose_file}"
            )

        append_log(
            self.deployment,
            f"Building with Docker Compose ({self.service.compose_file})...\n"
        )

        # Project name = service name (so containers are namespaced)
        live_project_name = self.service.name.lower().replace(' ', '-')
        project_name = live_project_name
        if getattr(self, "staged_only", False):
            project_name = f"{live_project_name}-staging-{str(self.deployment.id)[:8]}"

        # Validate compose file and resolve main service
        try:
            with open(compose_path, encoding="utf-8") as handle:
                compose_data = yaml.safe_load(handle) or {}
            compose_services = set((compose_data.get("services") or {}).keys())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise BuildError(f"Invalid compose file: {exc}") from exc

        main_svc = (self.service.compose_main_service or "").strip()
        if main_svc and main_svc not in compose_services:
            append_log(
                self.deployment,
                f"  ⚠️ compose_main_service '{main_svc}' not found; auto-detecting main service.\n"
            )
            main_svc = ""
        if not main_svc:
            main_svc = self._detect_compose_main_service(compose_path)

        if not main_svc or main_svc not in compose_services:
            available = ", ".join(sorted(compose_services)) or "(none)"
            raise BuildError(
                "Could not determine compose main service. "
                f"Set compose_main_service explicitly. Available services: {available}"
            )

        if self.service.compose_main_service != main_svc:
            self.service.compose_main_service = main_svc
            self.service.save(update_fields=["compose_main_service"])
            append_log(
                self.deployment,
                f"  ℹ️ Main compose service set to: {main_svc}\n"
            )

        override_path = self._write_compose_routing_override(main_svc, project_name)

        # Build env vars to inject (addons, runtime config)
        env = os.environ.copy()
        env['DOCKER_BUILDKIT'] = '1'
        # Merge compose .env file if present (Coolify parity: supports ${VAR} interpolation)
        compose_env_file = os.path.join(self.source_dir, '.env')
        if os.path.isfile(compose_env_file):
            for line in _read_env_file(compose_env_file):
                key, _, val = line.partition('=')
                if key and not key.startswith('#'):
                    env.setdefault(key.strip(), val.strip())
        for ev in self.service.env_vars.all():
            env[ev.key] = ev.value

        # Inject addon connection URLs
        from apps.addons.services.addon_provisioner import AddonProvisioner

        from apps.deployments.models.addons import Addon
        for addon in Addon.objects.filter(
            service=self.service, status='ACTIVE'
        ):
            env_key = AddonProvisioner.ENV_KEY_MAP.get(addon.addon_type)
            if env_key and addon.connection_url:
                env.setdefault(env_key, addon.connection_url)

        # Ensure scoped network exists
        network_name = self._resolve_service_network_name()

        # ─── Phase 1: Build images while old containers keep serving ───
        # Separating build from deploy eliminates the build time from the
        # downtime window. Old containers keep serving traffic during build.
        append_log(
            self.deployment,
            "  Building images (services stay live)...\n"
        )
        build_cmd = [
            'docker', 'compose',
            '-f', compose_path,
            '-p', project_name,
            'build',
        ]
        try:
            process = subprocess.run(
                build_cmd, check=True, cwd=self.source_dir, env=env,
                capture_output=True, text=True, timeout=3600,
            )
            output = redact_values(
                process.stdout + process.stderr, self.secret_values
            )
            if len(output) > 5000:
                output = output[-5000:] + '\n...(truncated)'
            append_log(self.deployment, output)
        except subprocess.CalledProcessError as e:
            full_err = redact_values(
                (e.stdout or '') + (e.stderr or ''), self.secret_values
            )
            append_log(self.deployment, full_err)
            raise BuildError(f"Compose build failed: {full_err[:500]}") from e

        # ─── Phase 2: Deploy from cached images (fast restart) ───
        # Images are already built — docker compose up replaces only containers
        # whose config changed, using the cached images. No rebuild needed.
        append_log(
            self.deployment,
            "  Deploying from cached images...\n"
        )
        cmd = [
            'docker', 'compose',
            '-f', compose_path,
            '-f', override_path,
            '-p', project_name,
            'up', '-d',
            '--remove-orphans',
        ]

        try:
            process = subprocess.run(
                cmd, check=True, cwd=self.source_dir, env=env,
                capture_output=True, text=True, timeout=3600,  # 60 minutes max
            )
            output = redact_values(
                process.stdout + process.stderr, self.secret_values
            )
            if len(output) > 5000:
                output = output[-5000:] + '\n...(truncated)'
            append_log(self.deployment, output)
        except subprocess.CalledProcessError as e:
            full_err = redact_values(
                (e.stdout or '') + (e.stderr or ''), self.secret_values
            )
            append_log(self.deployment, full_err)
            raise BuildError(f"Compose deploy failed: {full_err[:500]}") from e
        finally:
            try:
                if os.path.exists(override_path):
                    os.remove(override_path)
            except OSError:
                pass

        append_log(
            self.deployment,
            f"  📛 Traefik labels prepared at container creation for {main_svc}\n"
        )

        # Derive the compose container name for post-deploy hooks & health checks
        container_name = f"{project_name}-{main_svc}-1"

        # Post-deploy hooks (e.g., Prisma migrate) for ai-router / litellm templates
        self._post_deploy_hooks(container_name)

        # Store container name for health checking in _deploy_container
        self.image_name = f"compose:{container_name}"



    def _get_build_context(self) -> str:
        """Resolve root directory."""
        if not self.source_dir:
            raise BuildError("Source directory is not available for build context")
        root_dir = (self.service.root_directory or "/").strip()
        if root_dir in ("", "/", ".", "./"):
            return self.source_dir

        candidate = os.path.abspath(os.path.join(self.source_dir, root_dir.lstrip("/\\")))
        if not candidate.startswith(os.path.abspath(self.source_dir)):
            raise BuildError("root_directory must be inside the repo")
        if not os.path.isdir(candidate):
            raise BuildError(f"Directory not found: {root_dir}")
        return candidate



    def _find_dockerfile(self, context_dir: str) -> str | None:
        """Locate Dockerfile in context or subdirs."""
        # Direct check
        direct = os.path.join(context_dir, "Dockerfile")
        if os.path.isfile(direct):
            return direct

        # Shallow scan
        for entry in os.listdir(context_dir):
            candidate = os.path.join(context_dir, entry, "Dockerfile")
            if os.path.isdir(os.path.join(context_dir, entry)) and os.path.isfile(candidate):
                return candidate
        return None



    def _pull_cached_image(self) -> bool:
        """Pull a previously built tag from the local registry on cache miss.

        Retention prunes old tags from the daemon but keeps the rollback
        window in the registry; a pull restores the exact image in seconds
        instead of rebuilding for minutes. The daemon may lack registry
        creds at this point (login happens later in the build flow), so any
        auth/network failure simply returns False and the normal build
        proceeds. Never raises.
        """
        try:
            from django.conf import settings as _settings
            registry = (getattr(_settings, "CONTAINER_REGISTRY_URL", "") or "").strip()
            registry = registry.split("://")[-1].rstrip("/")
            if not registry or not self.image_name:
                return False
            # Already qualified (registry-prefixed at construction) — pull
            # as-is instead of double-prefixing.
            remote = self.image_name
            first = remote.split("/")[0] if "/" in remote else ""
            if "." not in first and ":" not in first:
                remote = f"{registry}/{self.image_name}"
            try:
                from apps.cloud.docker_client import get_docker_client
                client = get_docker_client()
            except Exception:
                import docker as docker_lib
                client = docker_lib.from_env()
            pulled = client.images.pull(remote)
            repo, _, tag = self.image_name.rpartition(":")
            pulled.tag(repo or self.image_name, tag or "latest")
            append_log(
                self.deployment,
                f"✓ Pulled previously built image {remote} — skipping build.\n",
            )
            return True
        except Exception as exc:
            logger.debug("Pull-on-miss failed for %s: %s", self.image_name, exc)
            return False

    def _build_with_docker(self, context_dir: str, dockerfile_path: str):
        """Execute Docker build, BuildKit-first with classic fallback.

        Prefers daemon-embedded BuildKit via the plain CLI (cache mounts,
        --secret mounts, registry cache) via `_build_with_docker_engine`,
        which falls back to the docker-py Engine-API path
        (`_build_via_docker_py`) only on infrastructure-class failures.
        Set SMSLY_DOCKER_BUILDER=classic to force the legacy path.
        The CLI talks to the daemon over HTTP via DOCKER_HOST
        (socket-proxy in compose mode), same transport as the SDK path.
        """
        # ── Runtime Hardening: patch outdated base images ──
        self._patch_dockerfile_for_runtime(dockerfile_path)

        append_log(
            self.deployment,
            f"Building with Docker ({os.path.basename(dockerfile_path)})...\n"
        )

        # Smart build-arg detection: only pass non-secret build-args.
        # Secrets are injected at runtime via BuildKit --mount=type=secret,
        # never baked into the image or visible in docker history.
        from apps.cloud.services.build_constants import is_secret_env_var

        env_map = {env.key: env.value for env in self.service.env_vars.all()}
        build_args_dict = {}
        defined_args = extract_dockerfile_arg_names(dockerfile_path)
        if defined_args:
            for k in defined_args:
                if k in env_map:
                    if is_secret_env_var(k):
                        logger.info("Skipping secret build-arg: %s", k)
                        continue
                    build_args_dict[k] = env_map[k]
        else:
            # Fallback: pass frontend-like vars (safe, non-secret)
            for k, v in env_map.items():
                if k.startswith(("NEXT_PUBLIC_", "VITE_", "PUBLIC_")):
                    build_args_dict[k] = v

        # ── BuildKit secret resolution ──────────────────────────────────────
        # The platform's own GitHub token (App installation or OAuth) is
        # resolved here and injected as a build-arg so that RUN commands
        # (e.g. ``pip install git+https://${GITHUB_TOKEN}@github.com/...``)
        # can access private repos.  The token is NOT passed through the
        # normal build-arg filter above because it ends in _TOKEN and would
        # be skipped as a secret.
        #
        # docker-py (legacy Engine API) does not support BuildKit secret
        # mounts, so the token is merged into buildargs at build time.
        # It will be visible in ``docker history`` — acceptable for
        # platform-internal tokens that are short-lived (1-hour App tokens).
        #
        # Token priority (see utils.get_github_token_for_repo):
        #   1. GitHub App installation token — repo-scoped, 1-hour expiry
        #   2. Service owner's OAuth token — broad fallback
        #   3. None — Dockerfile anonymous-install fallback path
        build_secrets: dict[str, str] = {}
        try:
            from apps.deployments.utils import get_github_token_for_repo
            # Determine which shared repo the Dockerfile needs access to.
            # Defaults to smsly-shared; overridable via service env var.
            shared_repo = env_map.get(
                "SMSLY_SHARED_REPO", "SMSLYCLOUD/smsly-shared"
            )
            service_owner = getattr(self.service, "owner", None)
            gh_token = get_github_token_for_repo(service_owner, shared_repo)
            if gh_token:
                build_secrets["github_token"] = gh_token
                # Also inject as GITHUB_TOKEN so RUN commands that reference
                # ${GITHUB_TOKEN} without a Dockerfile ARG declaration work.
                build_secrets["GITHUB_TOKEN"] = gh_token
                append_log(
                    self.deployment,
                    "GitHub token resolved via App/OAuth — "
                    "injected as build-arg for private repo access.\n",
                )
            else:
                append_log(
                    self.deployment,
                    "⚠ No GitHub token available — private pip installs may fail. "
                    "Configure GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY or connect GitHub.\n",
                )
        except Exception as _exc:
            logger.warning(
                "Failed to resolve GitHub token for build secrets: %s", _exc
            )

        # Pre-flight: remove any orphaned buildkit containers that can
        # block the Docker build (common after a previous build crash/timeout).
        _cleanup_stuck_buildkit()

        # NOTE: The previous ``_ensure_docker_driver`` step (which used
        # ``docker buildx inspect`` to confirm the default builder uses
        # the docker driver) is no longer required: docker-py talks
        # straight to the Docker Engine API, which always uses the
        # legacy docker driver for the streamed-context build path.
        # Skipping it also removes a subprocess call into the missing
        # ``docker`` CLI.

        # Authenticate with the private registry before building so the
        # Docker daemon can pull base images (FROM lines in Dockerfile)
        # from an auth-enabled registry without 403 errors.  Uses
        # docker-py so this works without the ``docker`` CLI binary.
        #
        # Resolution priority:
        #   1. Per-service RegistryCredential (for pulling third-party images)
        #   2. ScopedRegistry chain (Project → Team → Organization)
        #   3. PlatformConfig global fallback
        _reg_user = ""
        _reg_pass = ""
        registry_url = ""

        # Check per-service RegistryCredential first (third-party image pulls)
        if getattr(self.service, 'registry_credential_id', None) and self.service.registry_credential.is_active:
            _reg_user = self.service.registry_credential.username
            _reg_pass = self.service.registry_credential.password
            if self.service.registry_credential.registry_url:
                registry_url = self.service.registry_credential.registry_url.replace("https://", "").replace("http://", "").split("/")[0]

        # Fall back to scoped registry chain
        if not registry_url:
            from apps.deployments.models.registry_scope import ScopedRegistry
            scope_obj = self.service.project or self.service.owner
            registry_info = ScopedRegistry.resolve_registry_credentials(scope_obj)
            registry_url = (registry_info.get("url") or "").split("://")[-1]
            _reg_user = _reg_user or registry_info.get("username", "")
            _reg_pass = _reg_pass or registry_info.get("password", "")

        # Final fallback to PlatformConfig / settings
        if not registry_url:
            registry_url = (
                PlatformConfig.get_config_value('container_registry_url')
                or getattr(settings, 'CONTAINER_REGISTRY_URL', '') or "registry.smsly.cloud"
            ).split("://")[-1]
        _reg_user = _reg_user or PlatformConfig.get_config_value('registry_user') or getattr(settings, 'REGISTRY_USER', '')
        _reg_pass = _reg_pass or PlatformConfig.get_config_value('registry_password') or getattr(settings, 'REGISTRY_PASSWORD', '')

        if _reg_user and _reg_pass:
            try:
                from apps.cloud.docker_client import get_docker_client
                client = get_docker_client()
                client.login(
                    username=_reg_user,
                    password=_reg_pass,
                    registry=registry_url,
                )
            except Exception as exc:
                logger.warning(
                    "Docker login attempt failed (%s); proceeding without auth",
                    exc,
                )

        # Only use cache_from when the image is registry-qualified.
        # Bare image names (e.g. "smsly/name:tag") cause BuildKit to
        # resolve them to docker.io, triggering "insufficient_scope"
        # errors when the repo doesn't exist or requires auth.
        image_name = self.image_name or ""
        cache_from, build_args_dict = _registry_cache_settings(
            image_name, build_args_dict)

        self._build_with_docker_engine(
            context_dir=context_dir,
            dockerfile_path=dockerfile_path,
            image_name=image_name,
            build_args_dict=build_args_dict,
            cache_from=cache_from,
            build_secrets=build_secrets,
        )

    def _build_with_docker_engine(
        self,
        context_dir: str,
        dockerfile_path: str,
        image_name: str,
        build_args_dict: dict,
        cache_from: list,
        build_secrets: dict,
    ):
        """BuildKit dispatcher with classic fallback.

        Tries daemon-embedded BuildKit via the plain CLI (cache mounts,
        secret mounts, registry cache) unless SMSLY_DOCKER_BUILDER=classic.
        The plain CLI carries no builder selection, so there is no client
        state to rot (the 2026-09-24 outage: phantom docker-container
        builder + refused create silently downgraded every build).
        Falls back to the legacy Engine-API path only on
        infrastructure-class failures (missing CLI, unreachable daemon).
        Dockerfile errors and timeouts raise immediately — retrying those
        on a second engine just burns another full build.
        """
        if _use_buildx():
            try:
                return self._build_with_buildkit(
                    context_dir=context_dir,
                    dockerfile_path=dockerfile_path,
                    tag=image_name,
                    buildargs=build_args_dict,
                    cache_from=cache_from,
                    secrets=build_secrets,
                )
            except BuildError:
                raise
            except Exception as exc:
                if _is_buildx_infra_error(str(exc)):
                    append_log(
                        self.deployment,
                        "BuildKit unavailable "
                        f"({str(exc)[:120]}) — falling back to classic builder...\n",
                    )
                else:
                    raise BuildError(
                        f"Docker build failed: {str(exc)[-500:]}") from exc
        self._build_via_docker_py(
            context_dir=context_dir,
            dockerfile_path=dockerfile_path,
            tag=image_name,
            buildargs=build_args_dict,
            cache_from=cache_from,
            secrets=build_secrets,
        )



    def _drain_build_log_tail(self, build_log) -> str:
        """Best-effort drain of an abandoned SDK build stream.

        When ``client.images.build`` raises, its output generator is
        left holding the daemon-side reason. Consume it boundedly and
        return the redacted tail ("" when there is nothing to drain).
        Never raises — diagnostics must not mask the original failure.
        """
        if build_log is None:
            return ""
        lines: list = []
        try:
            for entry in build_log:
                if not isinstance(entry, dict):
                    continue
                text = entry.get("error") or entry.get("errorDetail", {}).get("message")
                if not text:
                    text = entry.get("stream") or ""
                if text:
                    lines.append(str(text).strip())
        except Exception as exc:
            lines.append(f"[stream ended: {exc}]")
        tail = "\n".join(line for line in lines if line)
        tail = tail[-(BUILD_FAIL_TAIL_LINES * 200):]
        tail = "\n".join(tail.splitlines()[-BUILD_FAIL_TAIL_LINES:])
        redacted = redact_values(
            tail[-BUILD_FAIL_TAIL_CHARS:],
            getattr(self, "secret_values", None) or [],
        )
        if not redacted.strip():
            return ""
        return "\n[docker daemon build-output tail]\n" + redacted + "\n"

    def _build_with_buildkit(
        self,
        context_dir: str,
        dockerfile_path: str,
        tag: str,
        buildargs: dict,
        cache_from: list,
        secrets: dict[str, str] | None = None,
    ):
        """Build with daemon-embedded BuildKit (no buildx client state).

        Uses plain ``DOCKER_BUILDKIT=1 docker build``: cache mounts,
        genuine --secret mounts, and registry layer caching all work, but
        NOTHING depends on buildx builder selection — the 2026-09-24
        outage was a phantom docker-container `default` builder (plus a
        refused `create --driver docker`) silently downgrading every
        build to classic. Single attempt — the caller falls back to
        classic on infrastructure-class errors only; Dockerfile errors
        raise immediately (retrying them on another engine just burns a
        second full build).
        """
        from apps.deployments.constants import DOCKER_BUILD_TIMEOUT
        from apps.deployments.services.builders import (
            _platform_build_limits as _build_limits,
        )

        dockerfile_rel = os.path.relpath(dockerfile_path, context_dir)
        if dockerfile_rel.startswith(".."):
            dockerfile_rel = os.path.basename(dockerfile_path)
        try:
            with open(dockerfile_path, encoding="utf-8") as _df:
                _df_text = _df.read()
        except OSError:
            _df_text = ""
        secret_aware = "mount=type=secret" in _df_text

        secret_ids, secret_files = _stage_secret_files(secrets)
        try:
            mem_mb, cpu_pct, timeout_s = _build_limits()
        except Exception:
            mem_mb, cpu_pct, timeout_s = 10240, 400, DOCKER_BUILD_TIMEOUT
        # Ephemeral builder (opt-in): real per-build containment, since
        # the systemd scope below only constrains the CLI wrapper on
        # the docker driver. Container driver loads via output type.
        builder_name = None
        if _ephemeral_builder_enabled():
            dep_id = str(getattr(getattr(self, 'deployment', None), 'id', '') or 'x')
            builder_name = _create_ephemeral_builder(
                f"smsly-eph-{dep_id[:8]}", mem_mb, cpu_pct)
            if builder_name is None:
                append_log(
                    self.deployment,
                    "Ephemeral builder unavailable — falling back to default driver.\n",
                )
        try:
            if builder_name:
                # Ephemeral containment (opt-in): a dedicated
                # docker-container builder needs the buildx CLI.
                load_flag = ["--output", "type=docker"]
                cmd = [
                    "docker", "buildx", "build",
                    *load_flag, "--progress=plain",
                    "-t", tag, "-f", dockerfile_rel,
                    "--builder", builder_name,
                ]
            else:
                # Default path: daemon BuildKit via the plain CLI. No
                # builder selection, no --load/--output flags (plain
                # `docker build` loads into the daemon by default).
                cmd = [
                    "docker", "build",
                    "--progress=plain",
                    "-t", tag, "-f", dockerfile_rel,
                ]
            for k, v in (buildargs or {}).items():
                cmd += ["--build-arg", f"{k}={v}"]
            for sid, spath in zip(secret_ids, secret_files):
                cmd += ["--secret", f"id={sid},src={spath}"]
            if not secret_aware:
                # Compat: Dockerfiles written for ARG-based secrets can't
                # see --secret mounts. Same exposure as the classic path
                # (process list instead of image history — narrower window).
                for sid in secret_ids:
                    cmd += ["--build-arg", f"{sid}={(secrets or {}).get(sid, '')}"]
                if secret_ids:
                    append_log(
                        self.deployment,
                        "WARNING: Dockerfile is not secret-mount aware — secrets passed as build-arg (visible briefly in process list).\n"
                        "  Add RUN --mount=type=secret mounts to hide them fully.\n",
                    )
            for c in cache_from or []:
                cmd += ["--cache-from", f"type=registry,ref={c}"]
            cmd += ["--cache-to", "type=inline", context_dir]
            try:
                probe = subprocess.run(
                    ["systemd-run", "--version"],
                    capture_output=True, text=True, timeout=3,
                )
                if probe.returncode == 0:
                    cmd = [
                        "systemd-run", "--scope",
                        "-p", f"MemoryMax={mem_mb}M",
                        "-p", f"CPUQuota={cpu_pct}%",
                        "--", *cmd,
                    ]
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            append_log(
                self.deployment,
                f"Building with BuildKit ({os.path.basename(dockerfile_path)})...\n",
            )
            try:
                build_env = dict(os.environ)
                build_env["DOCKER_BUILDKIT"] = "1"
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=timeout_s, cwd=context_dir, env=build_env,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"docker CLI not found for build: {exc}") from exc
            tail = ((proc.stdout or "")[-20000:] + (proc.stderr or "")[-4000:])
            redacted = redact_values(tail, self.secret_values)
            if redacted.strip():
                append_log(self.deployment, redacted[-20000:] + "\n")
            if proc.returncode != 0:
                raise BuildError(f"Docker build failed: {redacted[-500:]}")
            # ── Output verification ──────────────────────────────────
            # Exit 0 is NOT success: with a docker-container builder
            # (buildx default flapping, ephemeral leftovers) and no
            # --load/--push, the image stays in the builder's private
            # cache and never lands in the daemon store — every later
            # step (push 404, pull 404, "local cache unavailable") then
            # fails mysteriously. Verify the tag exists; salvage with an
            # explicit --load rebuild (warm cache: seconds); else raise
            # infra-class so the dispatcher falls back to the Engine API.
            if not self._verify_built_tag(tag):
                append_log(
                    self.deployment,
                    f"Build finished but {tag} is missing from the daemon "
                    f"image store (builder did not load output). Retrying "
                    f"with explicit --load...\n",
                )
                if self._salvage_load(tag, cmd, context_dir, build_env, timeout_s):
                    append_log(
                        self.deployment,
                        f"Salvage --load succeeded: {tag} is now in the daemon store.\n",
                    )
                else:
                    raise RuntimeError(
                        "SMSLY_INFRA_NO_BUILD_OUTPUT: builder exit 0 but "
                        f"{tag} not found in daemon image store after "
                        f"--load salvage; falling back to Engine API build"
                    )
        finally:
            _cleanup_secret_files(secret_files)
            if builder_name:
                _remove_ephemeral_builder(builder_name)

    def _verify_built_tag(self, tag: str) -> bool:
        """True when *tag* exists in the daemon image store."""
        try:
            from apps.cloud.docker_client import get_docker_client
            get_docker_client().images.get(tag)
            return True
        except Exception as exc:
            logger.debug("Built-tag verification miss for %s: %s", tag, exc)
            return False

    def _salvage_load(
        self,
        tag: str,
        cmd: list,
        context_dir: str,
        build_env: dict,
        timeout_s: int,
    ) -> bool:
        """Re-run the build with explicit `--load` via buildx.

        Same args (warm builder cache: usually seconds), `--output
        type=docker` semantics forced so a docker-container driver
        loads the result into the daemon store. Returns True when the
        tag verifies afterwards. Never raises — failure falls through
        to the Engine-API fallback.
        """
        try:
            import shutil as _shutil
            if not _shutil.which("docker"):
                return False
            load_cmd = ["docker", "buildx", "build", "--load"]
            for arg in cmd:
                if arg in ("docker", "build", "buildx", "build"):
                    continue
                if arg == "systemd-run" or arg.startswith("-p") or arg in (
                    "--scope", "--",
                ):
                    continue
                if arg.startswith("MemoryMax=") or arg.startswith("CPUQuota="):
                    continue
                load_cmd.append(arg)
            # Drop a stale --output flag if the ephemeral path set one;
            # --load and --output are mutually exclusive.
            while "--output" in load_cmd:
                _idx = load_cmd.index("--output")
                del load_cmd[_idx:_idx + 2]
            proc = subprocess.run(
                load_cmd, capture_output=True, text=True,
                timeout=timeout_s, cwd=context_dir, env=build_env,
            )
            if proc.returncode != 0:
                logger.warning(
                    "Salvage --load rebuild failed (exit %s): %s",
                    proc.returncode,
                    (proc.stderr or proc.stdout or "")[-500:],
                )
                return False
            return self._verify_built_tag(tag)
        except Exception as exc:
            logger.warning("Salvage --load rebuild failed: %s", exc)
            return False

    def _build_via_docker_py(
        self,
        context_dir: str,
        dockerfile_path: str,
        tag: str,
        buildargs: dict,
        cache_from: list,
        secrets: dict[str, str] | None = None,
    ):
        """Build a Docker image via the docker-py SDK.

        Replaces the previous ``docker build`` subprocess invocation,
        which required the ``docker`` CLI binary in the container image.
        The SDK talks to the Docker daemon over HTTP via the shared
        ``apps.cloud.docker_client`` factory, which honours the
        ``DOCKER_HOST`` env var (pointing at the socket-proxy in compose
        mode) and falls back to the local socket otherwise.

        Build output is drained from the SDK generator and written to
        the deploy log on success (matching the previous subprocess
        behaviour of one redacted ``append_log`` call at the end).
        BuildKit cache errors get the same prune-and-retry treatment
        as the old ``_run_subprocess`` path.

        ``secrets`` is a ``{secret_id: secret_value}`` dict merged into
        build-args (visible in ``docker history`` — the legacy Engine API
        cannot do secret mounts; the buildx path stages real --secret
        mounts instead).
        """
        import io
        import tarfile

        from apps.cloud.docker_client import get_docker_client

        # The daemon needs the Dockerfile path relative to the build
        # context root inside the streamed tar.  If the Dockerfile lives
        # outside the context (rare; mostly monorepo edge cases), fall
        # back to the basename, which is the common case.
        dockerfile_rel = os.path.relpath(dockerfile_path, context_dir)
        if dockerfile_rel.startswith(".."):
            dockerfile_rel = os.path.basename(dockerfile_path)

        # ── Secret handling for the legacy Engine API ─────────────────────
        # docker-py cannot do BuildKit --secret mounts, so secrets go in
        # as buildargs (visible in `docker history` — logged below).
        # NOTE: no tmpfiles are staged here on purpose: the SDK never
        # consumes them, and staging-then-leaking them left token material
        # in /tmp indefinitely (2026-09-22). The buildx path below stages
        # files only around the subprocess call and deletes them in a
        # finally block.
        merged_buildargs = dict(buildargs or {})
        try:
            for secret_id, secret_val in (secrets or {}).items():
                # For docker-py, we must pass as buildargs, but we will
                # ensure the value is not persisted in the final image by
                # requiring the Dockerfile to use `ARG <id>` without `ENV`.
                # Log the security trade-off explicitly.
                merged_buildargs[secret_id] = secret_val
                merged_buildargs[secret_id.upper()] = secret_val
            if secrets:
                append_log(
                    self.deployment,
                    "WARNING: Build secrets passed as ARG (visible in `docker history` for this build).\n"
                    "  Migrate to BuildKit --secret mounts to hide them.\n"
                )
        except Exception as sec_exc:
            logger.warning("Failed to prepare build secrets: %s", sec_exc)

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            # Build a fresh tar of the build context on each attempt;
            # the buffer is consumed by the SDK and can't be re-read.
            # Enforce .dockerignore to prevent .git poisoning of shared cache
            try:
                di = os.path.join(context_dir, ".dockerignore")
                if not os.path.isfile(di):
                    with open(di, "w", encoding="utf-8") as f:
                        f.write(".git\n.gitignore\n.env\n*.log\n")
                else:
                    with open(di, encoding="utf-8") as f:
                        txt = f.read()
                    if ".git" not in txt:
                        with open(di, "a", encoding="utf-8") as f:
                            f.write("\n.git\n")
            except Exception:
                pass
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                # Respect .dockerignore via filtering (docker-py tar.add ignores it).
                # IMPORTANT: Never exclude the Dockerfile or .dockerignore itself —
                # some repos (e.g. Next.js starters) add "Dockerfile" to their
                # .dockerignore to keep it out of the image, but Docker needs it
                # in the build context to actually build.
                try:
                    import fnmatch
                    di_path = os.path.join(context_dir, ".dockerignore")
                    patterns = []
                    if os.path.isfile(di_path):
                        with open(di_path, encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#"):
                                    patterns.append(line)
                    # Files that must never be excluded from the build context tar
                    _always_include = {"Dockerfile", ".dockerignore"}
                    def _exclude(tarinfo):
                        basename = os.path.basename(tarinfo.name)
                        if basename in _always_include:
                            return tarinfo
                        rel = os.path.relpath(tarinfo.name, context_dir) if tarinfo.name != context_dir else "."
                        excluded = False
                        for pat in patterns:
                            negated = pat.startswith("!")
                            raw_pat = pat[1:] if negated else pat
                            if fnmatch.fnmatch(rel, raw_pat) or fnmatch.fnmatch(basename, raw_pat):
                                excluded = not negated
                        return None if excluded else tarinfo
                    tar.add(context_dir, arcname=".", filter=_exclude)
                except Exception:
                    tar.add(context_dir, arcname=".")
            tar_buffer.seek(0)

            build_log = None
            try:
                client = get_docker_client()
                build_kwargs: dict = {
                    "fileobj": tar_buffer,
                    "custom_context": True,
                    "tag": tag,
                    "dockerfile": dockerfile_rel,
                    "buildargs": merged_buildargs or None,
                    "cache_from": cache_from or None,
                    "rm": True,
                    "forcerm": True,
                }
                _image, build_log = client.images.build(**build_kwargs)
            except Exception as exc:
                err_str = str(exc) or type(exc).__name__
                redacted_err = redact_values(err_str, self.secret_values)
                # The SDK abandons the streamed build output when it raises —
                # drain it (bounded) so the daemon-side reason (OOMKilled,
                # executor failure, disk pressure, broken pipe under load)
                # lands in build_logs instead of vanishing. Without this,
                # failures present as a bare "Docker build failed" with a
                # clean-looking log (2026-09-15: 170 clean lines, zero error
                # text, 18 min gone, nothing to diagnose).
                streamed_tail = self._drain_build_log_tail(build_log)
                if streamed_tail:
                    append_log(self.deployment, streamed_tail)
                # BuildKit cache corruption -> prune and retry once
                if (
                    is_buildkit_cache_error(redacted_err)
                    and attempt < max_attempts
                ):
                    append_log(
                        self.deployment,
                        "BuildKit cache corruption detected. "
                        "Pruning cache and retrying once...\n",
                    )
                    prune_buildkit_cache()
                    continue
                append_log(self.deployment, redacted_err)
                if is_buildkit_cache_error(redacted_err):
                    raise BuildError(
                        "Docker cache corruption detected after "
                        "automatic recovery attempt."
                    ) from exc
                raise BuildError(
                    f"Docker build failed: {(streamed_tail or redacted_err)[-500:]}"
                ) from exc

            # Drain the build log generator
            log_chunks = []
            for entry in build_log:
                if not isinstance(entry, dict):
                    continue
                if entry.get("error"):
                    raise BuildError(entry["error"].strip())
                if entry.get("stream"):
                    log_chunks.append(entry["stream"])

            full_log = "".join(log_chunks)
            redacted = redact_values(full_log, self.secret_values)
            if len(redacted) > 20000:
                redacted = redacted[-20000:] + "\n...(truncated)"
            if redacted:
                append_log(self.deployment, redacted)
            return



    def _ensure_docker_driver(self):
        """
        Ensure the default buildx builder uses the docker driver.

        Returns True when the default builder is confirmed to be the
        docker driver (either it was already, or it was successfully
        recreated). Returns False when recreation was attempted but
        failed; the caller MUST refuse to proceed with the build in
        that case to avoid cascading buildx state changes across
        concurrent builds.

        Concurrency: protected by a class-level threading.Lock so two
        PipelineManager instances cannot race to rm/create the default
        builder simultaneously.
        """
        import subprocess
        lock = type(self)._buildx_driver_lock
        with lock:
            try:
                inspect = subprocess.run(
                    ["docker", "buildx", "inspect"],
                    capture_output=True, text=True, timeout=15
                )
            except FileNotFoundError:
                logger.warning(
                    "buildx_driver_inspect_skipped reason=docker_cli_not_found"
                )
                return True
            except subprocess.TimeoutExpired:
                logger.warning(
                    "buildx_driver_inspect_skipped reason=timeout"
                )
                return True
            except Exception as exc:
                logger.warning(
                    "buildx_driver_inspect_failed error=%s", exc,
                )
                return True

            if inspect.returncode != 0:
                logger.warning(
                    "buildx_driver_inspect_failed returncode=%s stderr=%s",
                    inspect.returncode,
                    (inspect.stderr or "").strip()[:200],
                )
                return True

            output = (inspect.stdout or "") + (inspect.stderr or "")
            if "Driver: docker" in output:
                return True

            logger.warning(
                "buildx_driver_recreation_started current_driver_non_docker"
            )
            try:
                rm = subprocess.run(
                    ["docker", "buildx", "rm", "default"],
                    capture_output=True, text=True, timeout=15
                )
            except FileNotFoundError as exc:
                logger.error(
                    "buildx_driver_recreation_failed step=rm reason=docker_cli_not_found error=%s",
                    exc,
                )
                return False
            except subprocess.TimeoutExpired:
                logger.error(
                    "buildx_driver_recreation_failed step=rm reason=timeout"
                )
                return False
            except Exception as exc:
                logger.error(
                    "buildx_driver_recreation_failed step=rm error=%s", exc,
                )
                return False
            if rm.returncode != 0:
                logger.error(
                    "buildx_driver_recreation_failed step=rm returncode=%s stderr=%s",
                    rm.returncode,
                    (rm.stderr or "").strip()[:200],
                )
                return False
            logger.info("buildx_driver_recreation_progress step=rm completed")

            try:
                create = subprocess.run(
                    ["docker", "buildx", "create", "--name=default",
                     "--driver=docker", "--use"],
                    capture_output=True, text=True, timeout=15
                )
            except FileNotFoundError as exc:
                logger.error(
                    "buildx_driver_recreation_failed step=create reason=docker_cli_not_found error=%s",
                    exc,
                )
                return False
            except subprocess.TimeoutExpired:
                logger.error(
                    "buildx_driver_recreation_failed step=create reason=timeout"
                )
                return False
            except Exception as exc:
                logger.error(
                    "buildx_driver_recreation_failed step=create error=%s", exc,
                )
                return False
            if create.returncode != 0:
                logger.error(
                    "buildx_driver_recreation_failed step=create returncode=%s stderr=%s",
                    create.returncode,
                    (create.stderr or "").strip()[:200],
                )
                return False

            logger.info(
                "buildx_driver_recreation_succeeded driver=docker"
            )
            return True



    def _patch_dockerfile_for_runtime(self, dockerfile_path: str):
        """
        Scan and patch the Dockerfile for outdated or incompatible runtimes.
        Currently handles: Node.js 18 -> 20 (for Next.js compatibility).
        """
        try:
            with open(dockerfile_path, encoding="utf-8") as f:
                content = f.read()

            new_content = content
            patches = []

            # 1. Node.js 18 -> 20
            # Matches: node:18, node:18-alpine, node:18-slim, etc.
            if re.search(r"FROM\s+node:18", content, re.IGNORECASE):
                new_content = re.sub(
                    r"(FROM\s+node:)18",
                    r"\1 20",
                    new_content,
                    flags=re.IGNORECASE
                ).replace("node: 20", "node:20") # Cleanup any accidental space
                patches.append("Node.js 18 → 20")

            if content != new_content:
                with open(dockerfile_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

                patch_list = ", ".join(patches)
                append_log(
                    self.deployment,
                    f"🛡️ Runtime Hardening: Patched Dockerfile base image ({patch_list})\n"
                )
                logger.info("Patched Dockerfile %s: %s", dockerfile_path, patch_list)
        except Exception as exc:
            logger.warning("Failed to patch Dockerfile %s: %s", dockerfile_path, exc)



    def _detect_django_project_module(self, context_dir: str) -> str:
        """Best-effort discovery of Django project module for gunicorn startup."""
        for root, _, files in os.walk(context_dir):
            if "settings.py" not in files:
                continue
            if "__init__.py" not in files:
                continue
            rel_path = os.path.relpath(root, context_dir).strip(".\\/")
            if rel_path:
                return rel_path.replace(os.sep, ".")
        return ""



    def _resolve_nixpacks_start_command(self, context_dir: str) -> str:
        """Infer an explicit start command when the repo does not declare one."""
        explicit = str(self.service.start_command or "").strip()
        if explicit:
            return explicit

        procfile_path = os.path.join(context_dir, "Procfile")
        if os.path.isfile(procfile_path):
            try:
                with open(procfile_path, encoding="utf-8", errors="replace") as handle:
                    for raw_line in handle:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or ":" not in line:
                            continue
                        proc_name, cmd = line.split(":", 1)
                        if proc_name.strip().lower() == "web" and cmd.strip():
                            return cmd.strip()
            except OSError:
                pass

        package_json = os.path.join(context_dir, "package.json")
        if os.path.isfile(package_json):
            try:
                with open(package_json, encoding="utf-8", errors="replace") as handle:
                    pkg = json.load(handle)
                scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
                if isinstance(scripts, dict) and str(scripts.get("start", "")).strip():
                    if os.path.isfile(os.path.join(context_dir, "pnpm-lock.yaml")):
                        return "pnpm start"
                    if os.path.isfile(os.path.join(context_dir, "yarn.lock")):
                        return "yarn start"
                    if os.path.isfile(os.path.join(context_dir, "bun.lockb")):
                        return "bun run start"
                    return "npm run start"
            except (OSError, json.JSONDecodeError):
                pass

        if os.path.isfile(os.path.join(context_dir, "manage.py")):
            project_module = self._detect_django_project_module(context_dir)
            if project_module:
                return (
                    f"gunicorn {project_module}.wsgi:application "
                    "--bind 0.0.0.0:${PORT:-8000}"
                )
            return "python manage.py runserver 0.0.0.0:${PORT:-8000}"

        main_py = os.path.join(context_dir, "main.py")
        if os.path.isfile(main_py):
            try:
                with open(main_py, encoding="utf-8", errors="replace") as handle:
                    main_text = handle.read()
                if "FastAPI(" in main_text or "fastapi.FastAPI(" in main_text:
                    return "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"
            except OSError:
                pass

        return ""



    def _build_with_nixpacks(self, context_dir: str):
        """Execute Nixpacks build."""
        append_log(self.deployment, "Building with Nixpacks...\n")
        import re
        env_map = {}
        for env in self.service.env_vars.all():
            if isinstance(env.value, str) and re.search(r"\{\{.*?\}\}", env.value):
                append_log(
                    self.deployment,
                    f"SKIP: env var {env.key} has unresolved placeholder {env.value} — "
                    "addon may not be provisioned yet.\n",
                )
                continue
            env_map[env.key] = env.value
        start_cmd = self._resolve_nixpacks_start_command(context_dir)
        allow_missing_start = not bool(start_cmd)

        if start_cmd:
            append_log(
                self.deployment,
                f"Using start command for Nixpacks: {start_cmd}\n",
            )
        else:
            append_log(
                self.deployment,
                "No start command detected; building with --no-error-without-start.\n",
            )

        if not self.image_name:
            raise BuildError(
                "Cannot run Nixpacks build: no image_name was generated"
            )

        try:
            result = NixpacksBuilder.build_image(
                source_dir=context_dir,
                image_name=self.image_name,
                env_vars=env_map,
                start_cmd=start_cmd or None,
                allow_missing_start=allow_missing_start,
            )
        except RuntimeError as exc:
            # Defensive retry path for environments where start command probing is unstable.
            if "no start command could be found" not in str(exc).lower():
                raise
            append_log(
                self.deployment,
                "Retrying Nixpacks build with --no-error-without-start.\n",
            )
            result = NixpacksBuilder.build_image(
                source_dir=context_dir,
                image_name=self.image_name,
                env_vars=env_map,
                start_cmd=None,
                allow_missing_start=True,
            )

        # NixpacksBuilder returns dict with stdout/stderr
        if result.get("stderr"):
            redacted_stderr = redact_values(result['stderr'], self.secret_values)
            append_log(self.deployment, f"[Nixpacks Log]\n{redacted_stderr}\n")



    def _run_subprocess(self, cmd: list, cwd: str):
        """Helper to run shell commands with logging."""
        env = os.environ.copy()
        # BuildKit MUST be enabled for layer caching. Without it, every build
        # re-downloads all dependencies from scratch (extremely slow).
        # If BuildKit causes cache corruption, the auto-prune below handles it.
        env["DOCKER_BUILDKIT"] = "1"
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                process = subprocess.run(
                    cmd, check=True, cwd=cwd, env=env,
                    capture_output=True, text=True, timeout=3600  # 60 minutes max
                )
                # Log output (redacted)
                output = redact_values(process.stdout + process.stderr, self.secret_values)
                if len(output) > 20000:
                    output = output[-20000:] + "\n...(truncated)"
                append_log(self.deployment, output)
                return

            except subprocess.TimeoutExpired:
                # Kill stuck buildkit containers before retry
                append_log(
                    self.deployment,
                    "Build timed out. Cleaning up stuck BuildKit containers and retrying...\n"
                )
                _cleanup_stuck_buildkit()
                if attempt >= max_attempts:
                    raise BuildError("Build timed out after maximum retries.")
                continue

            except subprocess.CalledProcessError as e:
                full_err = redact_values(e.stdout + e.stderr, self.secret_values)

                if is_buildkit_cache_error(full_err) and attempt < max_attempts:
                    append_log(
                        self.deployment,
                        "BuildKit cache corruption detected. Pruning cache and retrying once...\n"
                    )
                    prune_buildkit_cache()
                    continue

                append_log(self.deployment, full_err)
                if is_buildkit_cache_error(full_err):
                    raise BuildError(
                        "Docker cache corruption detected after automatic recovery attempt."
                    ) from e
                raise BuildError("Command failed") from e
