import logging
import os
import subprocess

from apps.deployments.utils import append_log, find_binary, update_stage


logger = logging.getLogger(__name__)


class SigningMixin:

    def _cosign_enabled(self) -> tuple[bool, bool]:
        """Return (cosign_enabled, cosign_require_verify) from PlatformConfig."""
        try:
            from apps.deployments.models.core import PlatformConfig
            config = PlatformConfig.load()
            return (
                bool(getattr(config, 'cosign_enabled', True)),
                bool(getattr(config, 'cosign_require_verification', False)),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            return True, False

    def _cosign_env(self) -> dict:
        """Build env for cosign subprocess."""
        env = os.environ.copy()
        env["COSIGN_EXPERIMENTAL"] = "1"
        return env

    def _is_local_registry(self) -> bool:
        """True if the target image reference points at the platform's
        own registry (any of its addresses: registry:5000, loopback, WG
        mesh IP, or the configured public bind IP).

        Previously this only inspected CONTAINER_REGISTRY_URL's host —
        with multi-node deployments the image can be qualified with the
        node-routable address (10.100.0.1:5000 / public IP:5000), which
        serve the same SELF-SIGNED TLS cert and need the same
        --allow-insecure-registry (or certs.d trust) handling.
        """
        try:
            from apps.deployments.services.registry_routing import (
                is_master_registry_ref,
            )
            if self.image_name and is_master_registry_ref(self.image_name):
                return True
            # Fall back to the env-var host check (covers registry URLs
            # that are not image-qualified, e.g. keyless verify targets).
            raw = os.environ.get("CONTAINER_REGISTRY_URL", "")
            for scheme in ("https://", "http://"):
                if raw.startswith(scheme):
                    raw = raw[len(scheme):]
            host = raw.split("/")[0].split(":")[0]
            return host in ("", "registry", "127.0.0.1", "localhost")
        except Exception:
            return True

    def _get_cosign_version(self, cosign_bin: str) -> tuple[int, ...]:
        """Return cosign major.minor version as a tuple, e.g. (3, 1)."""
        try:
            result = subprocess.run(
                [cosign_bin, "version"],
                capture_output=True, text=True, timeout=5,
            )
            for line in (result.stdout + result.stderr).splitlines():
                if "GitVersion:" in line:
                    ver_str = line.split("GitVersion:")[-1].strip().lstrip("v")
                    parts = ver_str.split(".")
                    return (int(parts[0]), int(parts[1])) if len(parts) >= 2 else (int(parts[0]),)
        except Exception:
            pass
        return (0,)

    def _get_cosign_pubkey(self, cosign_bin: str, key_path: str, cosign_env: dict) -> str | None:
        """Extract public key from a cosign private key file. Returns path to pub key."""
        try:
            result = subprocess.run(
                [cosign_bin, "public-key", "--key", key_path],
                capture_output=True, text=True, timeout=10, env=cosign_env,
            )
            if result.returncode == 0 and "PUBLIC KEY" in result.stdout:
                pub_path = key_path.rsplit(".", 1)[0] + ".pub"
                with open(pub_path, "w") as f:
                    f.write(result.stdout)
                return pub_path
        except Exception:
            pass
        return None

    def _create_nolog_signing_config(self, cosign_bin: str) -> str | None:
        """Create a cosign signing config without transparency log for v3+.

        Returns the path to the temp config file, or None on failure.
        """
        try:
            config_path = "/tmp/cosign-signing-config.json"
            result = subprocess.run(
                [cosign_bin, "signing-config", "create", "--rekor-url", ""],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                with open(config_path, "w") as f:
                    f.write(result.stdout)
                return config_path
        except Exception:
            pass
        return None

    # ── SIGN (runs before push) ─────────────────────────────────────────

    def _sign_image(self):
        """Sign the local image with Cosign before pushing to registry.

        Uses a private key when COSIGN_PRIVATE_KEY_PATH is set and the file
        is readable.  Falls back to keyless Sigstore only when the registry
        is external (has internet access to Fulcio/Rekor).
        """
        if not self.image_name:
            return

        cosign_enabled, _ = self._cosign_enabled()
        if not cosign_enabled:
            append_log(
                self.deployment,
                "Cosign signing is disabled (cosign_enabled=false). Skipping.\n",
            )
            return

        cosign_bin = find_binary("cosign")
        if not cosign_bin:
            append_log(
                self.deployment,
                "Cosign not installed — skipping image signing. "
                "Install Cosign for cryptographic image attestation.\n",
            )
            return

        update_stage(self.deployment, 'Sign', 'running')
        self._check_cancellation('Sign')

        try:
            key_path = os.environ.get("COSIGN_PRIVATE_KEY_PATH") or os.environ.get("COSIGN_KEY")
            cosign_env = self._cosign_env()

            # Check if key is actually readable (mounted into container).
            key_available = key_path and os.path.isfile(key_path) and os.access(key_path, os.R_OK)

            cosign_ver = self._get_cosign_version(cosign_bin)
            is_v3_plus = cosign_ver[0] >= 3

            if key_available:
                # Build base sign command
                sign_args = [cosign_bin, "sign", "--key", key_path, "--yes"]

                if is_v3_plus:
                    # cosign v3+ removed --tlog-upload=false; use signing-config
                    nolog_config = self._create_nolog_signing_config(cosign_bin)
                    if nolog_config:
                        sign_args += ["--signing-config", nolog_config]

                # For local registries with self-signed certs, skip TLS verify
                if self._is_local_registry():
                    sign_args += ["--allow-insecure-registry"]

                # Pass registry auth if configured
                reg_user = os.environ.get("REGISTRY_USER", "")
                reg_pass = os.environ.get("REGISTRY_PASSWORD", "")
                if reg_user and reg_pass:
                    sign_args += ["--registry-username", reg_user, "--registry-password", reg_pass]

                sign_args.append(self.image_name)
                cmd = sign_args

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=cosign_env)
                if result.returncode == 0:
                    append_log(self.deployment, f"Image signed with Cosign (private key, v{cosign_ver[0]}): {self.image_name}\n")
                else:
                    append_log(
                        self.deployment,
                        f"Cosign key signing failed (exit {result.returncode}): "
                        f"{(result.stderr or result.stdout or '').strip()[:300]}\n"
                    )
                    update_stage(self.deployment, 'Sign', 'failed')
                    return
            elif self._is_local_registry():
                append_log(
                    self.deployment,
                    "Cosign signing SKIPPED — local registry with no readable private key. "
                    "Keyless Sigstore requires internet access to Fulcio/Rekor. "
                    "To sign images, either: (1) mount COSIGN_PRIVATE_KEY_PATH into the "
                    "celery-deploy container, (2) configure COSIGN_OIDC_ISSUER, or "
                    "(3) use an external registry.\n",
                )
                update_stage(self.deployment, 'Sign', 'skipped')
                return
            else:
                cmd = [cosign_bin, "sign", "--yes", self.image_name]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=cosign_env)
                if result.returncode == 0:
                    append_log(self.deployment, f"Image signed with Cosign (keyless/Sigstore): {self.image_name}\n")
                else:
                    stderr_msg = (result.stderr or result.stdout or '').strip()[:300]
                    append_log(
                        self.deployment,
                        f"Cosign keyless signing failed (exit {result.returncode}): "
                        f"{stderr_msg}\n"
                    )
                    update_stage(self.deployment, 'Sign', 'failed')
                    return

            update_stage(self.deployment, 'Sign', 'success')

        except subprocess.TimeoutExpired:
            append_log(self.deployment, "Cosign signing timed out (60s). Skipping.\n")
            update_stage(self.deployment, 'Sign', 'skipped')
        except Exception as e:
            append_log(self.deployment, f"Cosign signing error (non-fatal): {e!s}\n")
            update_stage(self.deployment, 'Sign', 'skipped')

    # ── VERIFY (runs after push — registry has the signed image) ────────

    def _verify_signature(self):
        """Verify the image signature after it has been pushed to the registry.

        Runs after _push_image() so the registry is the source of truth.
        Non-fatal unless cosign_require_verification is enabled.
        """
        if not self.image_name:
            return

        cosign_enabled, cosign_require_verify = self._cosign_enabled()
        if not cosign_enabled:
            return

        cosign_bin = find_binary("cosign")
        if not cosign_bin:
            return

        # If signing was skipped (no key, local registry), don't verify either.
        sign_stage = next(
            (s for s in (self.deployment.pipeline_stages or []) if s.get('name') == 'Sign'),
            None,
        )
        if sign_stage and sign_stage.get('status') in ('skipped', 'failed'):
            if cosign_require_verify:
                update_stage(self.deployment, 'Verify', 'failed')
                raise SystemError(
                    f"Cosign signature verification skipped (signing was {sign_stage.get('status')}) "
                    f"but cosign_require_verification is enabled. "
                    f"Image: {self.image_name}. "
                    f"Fix: configure a cosign key or disable cosign_require_verification."
                )
            return

        update_stage(self.deployment, 'Verify', 'running')
        self._check_cancellation('Verify')

        try:
            cosign_oidc_issuer = os.environ.get("COSIGN_OIDC_ISSUER", "")
            cosign_env = self._cosign_env()

            cosign_ver = self._get_cosign_version(cosign_bin)

            # Determine verify command based on signing method
            # If we signed with a private key, verify with the public key
            key_path = os.environ.get("COSIGN_PRIVATE_KEY_PATH") or os.environ.get("COSIGN_KEY")
            key_available = key_path and os.path.isfile(key_path) and os.access(key_path, os.R_OK)

            if key_available:
                # Extract public key from private key for verification
                # First check if .pub file already exists alongside the key
                pub_key = key_path.rsplit(".", 1)[0] + ".pub"
                if not os.path.isfile(pub_key):
                    # Extract from private key
                    pub_key = self._get_cosign_pubkey(cosign_bin, key_path, cosign_env)
                if pub_key and os.path.isfile(pub_key):
                    verify_cmd = [
                        cosign_bin, "verify",
                        "--key", pub_key,
                        self.image_name,
                    ]
                else:
                    # Fallback: no pub key, skip verification
                    append_log(self.deployment, "Could not find or extract public key from cosign key. Skipping verification.\n")
                    update_stage(self.deployment, 'Verify', 'skipped')
                    return
            elif cosign_oidc_issuer:
                verify_cmd = [
                    cosign_bin, "verify",
                    "--certificate-oidc-issuer", cosign_oidc_issuer,
                    self.image_name,
                ]
            else:
                verify_cmd = [
                    cosign_bin, "verify",
                    "--certificate-identity-regexp", ".*",
                    self.image_name,
                ]

            # For local registries with self-signed certs, skip TLS verify.
            # The flag belongs to the `verify` SUBCOMMAND — it must come
            # AFTER "verify", not before. insert(1, ...) previously put it
            # in the top-level position, cosign parsed it as the command
            # name and every verification failed with:
            #   Error: unknown command "registry:5000/..." for "cosign"
            if self._is_local_registry():
                verify_cmd.insert(2, "--allow-insecure-registry")

            # Pass registry auth if configured
            reg_user = os.environ.get("REGISTRY_USER", "")
            reg_pass = os.environ.get("REGISTRY_PASSWORD", "")
            if reg_user and reg_pass:
                verify_cmd += ["--registry-username", reg_user, "--registry-password", reg_pass]

            vresult = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=30, env=cosign_env)
            if vresult.returncode == 0:
                append_log(self.deployment, "Cosign signature verification PASSED.\n")
                update_stage(self.deployment, 'Verify', 'success')
            else:
                verify_msg = (
                    f"Cosign verification returned code {vresult.returncode}: "
                    f"{(vresult.stderr or vresult.stdout or '').strip()[:300]}\n"
                )
                if cosign_require_verify:
                    update_stage(self.deployment, 'Verify', 'failed')
                    raise SystemError(
                        f"Cosign signature verification FAILED and cosign_require_verification "
                        f"is enabled. Deploying unsigned images is not permitted. "
                        f"Image: {self.image_name}. "
                        f"Fix: sign the image or disable cosign_require_verification in Platform Settings."
                    )
                append_log(self.deployment, f"Cosign verification warning (non-fatal): {verify_msg}")
                update_stage(self.deployment, 'Verify', 'success')

        except SystemError:
            raise
        except subprocess.TimeoutExpired:
            append_log(self.deployment, "Cosign verification timed out (30s). Skipping.\n")
            update_stage(self.deployment, 'Verify', 'skipped')
        except Exception as e:
            append_log(self.deployment, f"Cosign verification error (non-fatal): {e!s}\n")
            update_stage(self.deployment, 'Verify', 'skipped')
