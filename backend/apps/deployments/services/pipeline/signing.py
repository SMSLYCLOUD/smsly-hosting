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
        """Return True if CONTAINER_REGISTRY_URL points to a local registry."""
        try:
            raw = os.environ.get("CONTAINER_REGISTRY_URL", "")
            for scheme in ("https://", "http://"):
                if raw.startswith(scheme):
                    raw = raw[len(scheme):]
            host = raw.split("/")[0].split(":")[0]
            return host in ("", "registry", "127.0.0.1", "localhost")
        except Exception:
            return True

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

            if key_available:
                cmd = [cosign_bin, "sign", "--key", key_path, "--tlog-upload=false", self.image_name]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=cosign_env)
                if result.returncode == 0:
                    append_log(self.deployment, f"Image signed with Cosign (private key): {self.image_name}\n")
                else:
                    append_log(
                        self.deployment,
                        f"Cosign key signing failed (exit {result.returncode}): "
                        f"{(result.stderr or result.stdout or '').strip()[:200]}\n"
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
                    stderr_msg = (result.stderr or result.stdout or '').strip()[:200]
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

            if cosign_oidc_issuer:
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

            vresult = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=30, env=cosign_env)
            if vresult.returncode == 0:
                append_log(self.deployment, "Cosign signature verification PASSED.\n")
                update_stage(self.deployment, 'Verify', 'success')
            else:
                verify_msg = (
                    f"Cosign verification returned code {vresult.returncode}: "
                    f"{(vresult.stderr or vresult.stdout or '').strip()[:200]}\n"
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
