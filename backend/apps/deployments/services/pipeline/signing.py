import logging
import os
import subprocess

from apps.deployments.utils import append_log, find_binary, update_stage


logger = logging.getLogger(__name__)


class SigningMixin:
    def _sign_image(self):
        """Sign the deployed image with Cosign (keyless Sigstore or private key).

        Reads cosign_enabled and cosign_require_verification from PlatformConfig
        so the settings page UI controls the pipeline behavior.
        Runs after a successful push. Non-fatal if cosign is not installed or
        signing fails — unless cosign_require_verification is enabled, in which
        case a failed verification raises SystemError.
        """
        if not self.image_name:
            return

        try:
            from apps.deployments.models.core import PlatformConfig
            config = PlatformConfig.load()
            cosign_enabled = bool(getattr(config, 'cosign_enabled', True))
            cosign_require_verify = bool(getattr(config, 'cosign_require_verification', False))
        except Exception:  # pylint: disable=broad-exception-caught
            cosign_enabled = True
            cosign_require_verify = False

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
            cosign_oidc_issuer = os.environ.get("COSIGN_OIDC_ISSUER", "")

            # Detect if registry is local (no internet for Fulcio/Rekor).
            _registry_host = ""
            try:
                _registry_url_raw = os.environ.get("CONTAINER_REGISTRY_URL", "")
                for _scheme in ("https://", "http://"):
                    if _registry_url_raw.startswith(_scheme):
                        _registry_url_raw = _registry_url_raw[len(_scheme):]
                _registry_host = _registry_url_raw.split("/")[0].split(":")[0]
            except Exception:
                pass
            _is_local_registry = _registry_host in ("", "registry", "127.0.0.1", "localhost")
            _has_oidc = bool(cosign_oidc_issuer)

            # Keyless Sigstore signing requires Fulcio (CA) and Rekor (transparency
            # log) which are internet-accessible services.  Skip for local-only
            # registries when no private key AND no OIDC issuer are configured.
            if not key_path and _is_local_registry and not _has_oidc:
                append_log(
                    self.deployment,
                    "Cosign signing SKIPPED — local registry with no private key or OIDC issuer. "
                    "Keyless Sigstore requires internet access to Fulcio/Rekor. "
                    "To sign images, either: (1) set COSIGN_PRIVATE_KEY_PATH, "
                    "(2) configure COSIGN_OIDC_ISSUER, or (3) use an external registry.\n",
                )
                update_stage(self.deployment, 'Sign', 'skipped')
                return

            # Build env for cosign subprocess — COSIGN_EXPERIMENTAL=1 enables
            # keyless signing via Fulcio/Rekor without requiring a private key.
            _cosign_env = os.environ.copy()
            _cosign_env["COSIGN_EXPERIMENTAL"] = "1"

            if key_path and os.path.exists(key_path):
                cmd = [cosign_bin, "sign", "--key", key_path, "--tlog-upload=false", self.image_name]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=_cosign_env)
                if result.returncode == 0:
                    append_log(self.deployment, f"Image signed with Cosign (private key): {self.image_name}\n")
                else:
                    append_log(
                        self.deployment,
                        f"Cosign key signing failed (exit {result.returncode}): "
                        f"{(result.stderr or result.stdout or '').strip()[:200]}\n"
                    )
            else:
                cmd = [cosign_bin, "sign", "--yes", self.image_name]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=_cosign_env)
                if result.returncode == 0:
                    append_log(self.deployment, f"Image signed with Cosign (keyless/Sigstore): {self.image_name}\n")
                else:
                    stderr_msg = (result.stderr or result.stdout or '').strip()[:200]
                    append_log(
                        self.deployment,
                        f"Cosign keyless signing failed (exit {result.returncode}): "
                        f"{stderr_msg}\n"
                    )

            # Verify — skip for local-only images or if no OIDC issuer configured.
            # For self-hosted keyless, verification requires the signing identity
            # to match.  Use --certificate-identity-regexp to accept any local signing.
            if cosign_oidc_issuer:
                verify_cmd = [
                    cosign_bin, "verify",
                    "--certificate-oidc-issuer", cosign_oidc_issuer,
                    self.image_name,
                ]
            else:
                # Self-hosted: accept any certificate identity (no OIDC issuer check)
                verify_cmd = [
                    cosign_bin, "verify",
                    "--certificate-identity-regexp", ".*",
                    self.image_name,
                ]
            vresult = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=30, env=_cosign_env)
            if vresult.returncode == 0:
                append_log(self.deployment, "Cosign signature verification PASSED.\n")
            else:
                verify_msg = (
                    f"Cosign verification returned code {vresult.returncode} "
                    f"(non-fatal for local-only images).\n"
                )
                if cosign_require_verify:
                    update_stage(self.deployment, 'Sign', 'failed')
                    raise SystemError(
                        f"Cosign signature verification FAILED and cosign_require_verification "
                        f"is enabled. Deploying unsigned images is not permitted. "
                        f"Image: {self.image_name}. "
                        f"Fix: sign the image or disable cosign_require_verification in Platform Settings."
                    )
                append_log(self.deployment, verify_msg)

            update_stage(self.deployment, 'Sign', 'success')

        except SystemError:
            raise
        except subprocess.TimeoutExpired:
            append_log(self.deployment, "Cosign signing timed out (60s). Skipping.\n")
            update_stage(self.deployment, 'Sign', 'skipped')
        except Exception as e:
            append_log(self.deployment, f"Cosign signing error (non-fatal): {e!s}\n")
            update_stage(self.deployment, 'Sign', 'skipped')
