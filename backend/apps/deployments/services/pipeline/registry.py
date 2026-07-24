import logging
import os
import subprocess

from apps.cloud.services.builder import NixpacksBuilder
from apps.deployments.utils import (
    append_log,
    is_deployment_local,
    log_exhaustive_push_diagnostics,
    redact_values,
    update_stage,
)


logger = logging.getLogger(__name__)


class RegistryMixin:
    def _ensure_registry_running(self):
        """Ensure the internal registry container is running (if using internal registry).

        Only applies when CONTAINER_REGISTRY_URL points to the platform's
        own registry (registry:5000, 127.0.0.1:5000, localhost:5000).
        For external registries this is a no-op.
        """
        from apps.deployments.models.registry_scope import ScopedRegistry

        registry_url = None
        try:
            scope_obj = self.service.project or self.service.owner
            registry_info = ScopedRegistry.resolve_registry_credentials(scope_obj)
            registry_url = (registry_info.get("url") or "").split("://")[-1]
        except Exception:
            pass

        if not registry_url:
            return

        # Only check for internal registries
        internal_markers = ('registry:', '127.0.0.1:', 'localhost:')
        if not any(registry_url.startswith(m) for m in internal_markers):
            return  # external registry — nothing to bootstrap

        # Match any container whose name includes the registry service name.
        # Compose names it smsly-hosting-registry-1, plain docker may differ.
        container_pattern = 'smsly-hosting-registry'

        try:
            result = subprocess.run(
                ['docker', 'ps', '-a', '--filter', f'name={container_pattern}',
                 '--format', '{{.Names}} {{.Status}}'],
                capture_output=True, text=True, timeout=5,
            )
            if not result.stdout.strip():
                append_log(self.deployment,
                    "Registry container not found. Run on the host:\n"
                    "  docker compose up -d --no-deps registry\n"
                    "Then retry the deployment.\n")
                return

            for line in result.stdout.strip().splitlines():
                parts = line.split(' ', 1)
                name = parts[0]
                status = parts[1] if len(parts) > 1 else ''
                if 'Up' not in status:
                    subprocess.run(
                        ['docker', 'start', name],
                        capture_output=True, text=True, timeout=15,
                    )
                    append_log(self.deployment, f"Registry container '{name}' was stopped; started it.\n")
        except Exception as e:
            append_log(self.deployment, f"Warning: could not check/start registry container: {e}\n")



    def _resolve_push_credentials(self) -> dict:
        """Resolve registry URL + credentials for push/login.

        Priority:
          1. ``deployment.registry_override`` (per-deployment override)
          2. ScopedRegistry chain (Project → Team → Organization)
          3. PlatformConfig global fallback (always returns something)

        URL is always stripped of ``http://`` / ``https://`` prefixes.
        Returns a dict with keys ``url``, ``username``, ``password``.
        """
        from apps.deployments.models.registry_scope import ScopedRegistry

        registry_url = ""
        reg_username = ""
        reg_password = ""

        # 1. Per-deployment override
        if self.deployment.registry_override:
            registry_url = self.deployment.registry_override.get("url") or ""
            reg_username = self.deployment.registry_override.get("username") or ""
            reg_password = self.deployment.registry_override.get("password") or ""

        # 2. ScopedRegistry chain — only walk scope objects (Project/Team/Org).
        #    self.service.owner is a User, not a scope entity, so we only pass project.
        if not registry_url:
            scope_obj = self.service.project  # may be None → falls to PlatformConfig
            registry_info = ScopedRegistry.resolve_registry_credentials(scope_obj)
            registry_url = (registry_info.get("url") or "").split("://")[-1].rstrip("/")
            reg_username = reg_username or registry_info.get("username") or ""
            reg_password = reg_password or registry_info.get("password") or ""

        # Normalise URL (strip scheme, trailing slash)
        for scheme in ("https://", "http://"):
            if registry_url.startswith(scheme):
                registry_url = registry_url[len(scheme):]
        registry_url = registry_url.rstrip("/")

        return {
            "url": registry_url,
            "username": reg_username,
            "password": reg_password,
        }



    def _ensure_registry_login(self, creds: dict | None = None):
        """Ensure Docker is logged in to the registry before pushing.

        Accepts pre-resolved *creds* dict (from ``_resolve_push_credentials``)
        so that login and push always use identical credentials without a
        duplicate DB round-trip.

        Runs ``docker login`` with configured credentials so that
        ``docker push`` / ``docker pull`` commands succeed without
        requiring manual pre-authentication on the host.
        """
        if creds is None:
            try:
                creds = self._resolve_push_credentials()
            except Exception as exc:
                append_log(self.deployment, f"Warning: could not resolve registry credentials: {exc}\n")
                return

        registry_url = creds.get("url") or ""
        reg_username = creds.get("username") or ""
        reg_password = creds.get("password") or ""

        if not registry_url or not reg_username or not reg_password:
            append_log(
                self.deployment,
                f"Warning: registry login skipped — missing credentials "
                f"(url={registry_url or 'MISSING'}, "
                f"user={'set' if reg_username else 'MISSING'}, "
                f"pass={'set' if reg_password else 'MISSING'})\n",
            )
            return

        try:
            login_proc = subprocess.run(
                ['docker', 'login', registry_url, '-u', reg_username, '--password-stdin'],
                input=reg_password, capture_output=True, text=True, timeout=15,
            )
            if login_proc.returncode == 0:
                append_log(self.deployment, f"Registry login successful ({registry_url}).\n")
            else:
                # SECURITY: don't log raw stderr — docker CLI can echo
                # malformed input including password characters.
                append_log(
                    self.deployment,
                    f"ERROR: registry login failed for {registry_url} "
                    f"(exit code {login_proc.returncode}). Push will likely fail. "
                    f"Check that registry_user/registry_password in PlatformConfig "
                    f"match the htpasswd file.\n",
                )
        except Exception as e:
            append_log(self.deployment, f"ERROR: could not login to registry {registry_url}: {e}\n")



    def _push_image(self):
        """Step 3: Push to Registry."""
        # Skip push for DOCKER type: image is already in the registry
        if self.service.deploy_type == 'DOCKER' and self.service.docker_image:
            return

        # ── Auto-ensure Docker network exists ─────────────────────
        self._ensure_docker_network()

        # ── Auto-ensure internal registry is running ──────────────
        self._ensure_registry_running()

        # ── Resolve registry URL and credentials once ────────────
        # Priority: 1) deployment.registry_override
        #           2) ScopedRegistry chain (Project → Team → Organization)
        #           3) PlatformConfig global fallback
        # Resolved here so that login and push always use the same creds.
        try:
            creds = self._resolve_push_credentials()
        except Exception as _cred_exc:
            append_log(self.deployment, f"Warning: could not resolve registry credentials: {_cred_exc}\n")
            creds = {"url": "", "username": "", "password": ""}

        registry_url = creds.get("url") or ""
        reg_username = creds.get("username") or ""
        reg_password = creds.get("password") or ""

        # ── Auto-login to registry using the same resolved creds ──
        self._ensure_registry_login(creds=creds)

        is_local = is_deployment_local(self.deployment)
        if not registry_url:
            if not is_local:
                raise SystemError(
                    "No registry URL configured. "
                    "A registry is required to push/pull images for remote node deployments. "
                    "Set a registry at the Organization, Team, or Project level, "
                    "or configure CONTAINER_REGISTRY_URL."
                )
            return

        update_stage(self.deployment, 'Push', 'running')
        self._check_cancellation('Push')

        try:
            append_log(self.deployment, f"Pushing to {registry_url}...\n")
            remote_tag, push_error = NixpacksBuilder.push_image(
                self.image_name,
                registry_url,
                username=reg_username,
                password=reg_password,
            )
            self.image_name = remote_tag

            # Determine if push reached the registry.
            # push_image() strips http(s):// from the URL when forming the tag,
            # so normalise registry_url the same way before comparing.
            _norm_prefix = registry_url or ""
            for _scheme in ('https://', 'http://'):
                if _norm_prefix.startswith(_scheme):
                    _norm_prefix = _norm_prefix[len(_scheme):]
            # Strip trailing slash so "127.0.0.1:5000/" and "127.0.0.1:5000" both work.
            _norm_prefix = _norm_prefix.rstrip('/')
            pushed_to_registry = bool(_norm_prefix and remote_tag.startswith(_norm_prefix))

            if pushed_to_registry:
                update_stage(self.deployment, 'Push', 'success')
                append_log(self.deployment, f"✓ Pushed: {remote_tag}\n")
                build_safe = log_exhaustive_push_diagnostics(self.deployment, registry_url, remote_tag)
                if not build_safe:
                    update_stage(self.deployment, 'Push', 'blocked')
                    raise SystemError(
                        "Deployment BLOCKED: Trivy scan found vulnerabilities exceeding "
                        "the configured severity threshold. Check the vulnerability report."
                    )
            else:
                if push_error:
                    append_log(self.deployment, f"Registry error details: {redact_values(push_error, self.secret_values)}\n")
                if not is_local:
                    raise SystemError(
                        f"Image push failed: Local fallback is not allowed for remote deployments. "
                        f"Target node requires a working registry to pull {remote_tag}."
                    )
                update_stage(self.deployment, 'Push', 'success')
                append_log(
                    self.deployment,
                    f"⚠ Registry unreachable; using local image: {remote_tag}\n",
                )

        except Exception as e:
            update_stage(self.deployment, 'Push', 'failed')
            raise SystemError(f"Registry push failed: {e}") from e

