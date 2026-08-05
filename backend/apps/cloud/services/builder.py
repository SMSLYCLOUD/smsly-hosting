"""Builder module."""
import json
import logging
import os
import shutil
import subprocess
from typing import Any

from django.conf import settings

from apps.deployments.constants import DOCKER_BUILD_TIMEOUT

logger = logging.getLogger(__name__)


# ─── Buildx fallback (Batch J) ──────────────────────────────────────
# The default ``docker`` driver buildx builder can corrupt after
# Docker daemon restarts, disk pressure on /var/lib/docker/buildx,
# or daemon upgrades. Symptoms: every Nixpacks build fails with
# ``failed to recreate the buildx default builder with the
# docker driver``. The ``default`` name is reserved and tied
# to a Docker context that can't be removed while active, so the
# operator has to switch context first.
#
# Self-heal: when the build fails with the buildx default
# error, we (a) create a ``docker-container`` driver fallback
# named ``smsly-fallback``, and (b) re-run the build with
# ``BUILDX_BUILDER=smsly-fallback``. The fallback builder
# spawns a fresh BuildKit container per build, which is
# resilient to daemon restarts.

_BUILDX_DEFAULT_BROKEN_MARKERS = (
    'failed to recreate the buildx default builder',
    'buildx default builder',
    'no such builder: default',
    'failed to compute cache key',
    "couldn't find buildx default",
)

# Capture the real CalledProcessError class at import time.
# Tests that mock ``subprocess`` (e.g. ``mock.patch.object(
# builder, 'subprocess', ...)``) would otherwise replace the
# class with a MagicMock, breaking the ``except`` clause
# below with a TypeError. We re-export the real class so the
# except can match a real exception instance.
_CalledProcessError = subprocess.CalledProcessError


def _is_buildx_default_broken(stderr: str) -> bool:
    """Return True if ``stderr`` looks like the buildx
    default-builder recreation error.
    """
    if not stderr:
        return False
    needle = stderr.lower()
    return any(marker in needle for marker in _BUILDX_DEFAULT_BROKEN_MARKERS)


def _ensure_buildx_fallback(fallback_name: str = 'smsly-fallback') -> tuple[bool, str]:
    """Create the docker-container fallback builder if it
    doesn't already exist.

    The ``docker-container`` driver spawns a BuildKit container
    per build, which is more resilient to corruption than the
    ``docker`` driver. The fallback name is operator-tunable
    via ``settings.BUILDX_FALLBACK_BUILDER`` (env var).

    Returns ``(created, status)`` where ``created`` is True if
    a new builder was created (or already existed) and ``status``
    is a short human-readable description for logs.
    """
    try:
        ls = subprocess.run(
            ['docker', 'buildx', 'ls'],
            capture_output=True, text=True, timeout=15,
        )
    except (_CalledProcessError, FileNotFoundError) as exc:
        return False, f'buildx ls failed: {exc}'

    if fallback_name in (ls.stdout or ''):
        return True, f'fallback {fallback_name!r} already exists'

    create = subprocess.run(
        [
            'docker', 'buildx', 'create',
            '--name', fallback_name,
            '--driver', 'docker-container',
            '--use',
        ],
        capture_output=True, text=True, timeout=60,
    )
    if create.returncode != 0:
        return False, (
            f'fallback create failed (rc={create.returncode}): '
            f'{create.stderr.strip() or create.stdout.strip()}'
        )
    return True, f'created fallback {fallback_name!r}'


def _buildx_fallback_builder_name() -> str:
    """Return the configured fallback builder name. Reads from
    ``settings.BUILDX_FALLBACK_BUILDER`` (env var) with a
    sensible default of ``smsly-fallback``.
    """
    return (
        getattr(settings, 'BUILDX_FALLBACK_BUILDER', None)
        or 'smsly-fallback'
    )


class NixpacksBuilder:
    """
    Wrapper around Nixpacks CLI to build container images from source.
    Supports build caching for faster subsequent builds.
    """

    # Default cache directory for Nixpacks builds
    CACHE_DIR = "/tmp/smsly/build-cache"

    @staticmethod
    def build_image(
        source_dir: str,
        image_name: str,
        env_vars: dict | None = None,
        cache_dir: str | None = None,
        start_cmd: str | None = None,
        allow_missing_start: bool = False,
    ) -> dict:
        """
        Builds a Docker image using Nixpacks.

        Args:
            source_dir: Path to source code
            image_name: Docker image tag
            env_vars: Environment variables for build
            cache_dir: Optional cache directory (defaults to CACHE_DIR)

        Returns dict with 'image_name', 'stdout', 'stderr' upon success.
        """
        if not os.path.exists(source_dir):
            raise FileNotFoundError(f"Source directory {source_dir} not found")

        # Ensure cache directory exists
        effective_cache_dir = cache_dir or NixpacksBuilder.CACHE_DIR
        os.makedirs(effective_cache_dir, exist_ok=True)

        # Ensure cargo bin is in PATH (common install location)
        cargo_bin = os.path.expanduser("~/.cargo/bin")
        if cargo_bin not in os.environ.get("PATH", "") and os.path.isdir(cargo_bin):
            os.environ["PATH"] = f"{cargo_bin}:{os.environ.get('PATH', '')}"

        if not shutil.which("nixpacks"):
            raise RuntimeError("Nixpacks binary not found. Please install nixpacks.")

        command = [
            "nixpacks",
            "build",
            source_dir,
            "--name", image_name,
            "--verbose",
            # Buildx docker-container driver requires explicit output.
            # "type=docker" loads image into local daemon so push_image() can find it.
            "--docker-output", "type=docker",
            # Use base name as cache key
            "--cache-key", image_name.split(":", maxsplit=1)[0],
        ]

        # Add inline cache for Docker layer caching
        command.extend(["--inline-cache"])

        if start_cmd and str(start_cmd).strip():
            command.extend(["--start-cmd", str(start_cmd).strip()])
        elif allow_missing_start:
            command.append("--no-error-without-start")

        # Build-arg secret hints: env vars detected as secrets are
        # NOT passed as --env to nixpacks to avoid leaking into build
        # layers and process listings.  They are injected at runtime.
        from apps.cloud.services.build_constants import is_secret_env_var

        if env_vars:
            for k, v in env_vars.items():
                if is_secret_env_var(k):
                    logger.info("Skipping secret env for nixpacks build: %s", k)
                    continue
                command.extend(["--env", f"{k}={v}"])

        logger.info(
            f"Starting Nixpacks build for {image_name} (cache: {effective_cache_dir})...")

        try:
            # Run the build process (bounded so a hung Docker build cannot
            # block a Celery worker forever)
            process = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=DOCKER_BUILD_TIMEOUT,
                env={**os.environ, "NIXPACKS_CACHE_DIR": effective_cache_dir}
            )
            # Log build output for debugging
            if process.stdout:
                logger.info(f"Nixpacks stdout:\n{process.stdout[-2000:]}")
            if process.stderr:
                logger.info(f"Nixpacks stderr:\n{process.stderr[-2000:]}")

            # ─── Batch K: security scan with Trivy ──────────────────
            # Scan the freshly built image for vulnerabilities.
            # Controlled by settings.TRIVY_ENABLED and
            # settings.TRIVY_FAIL_ON_SEVERITY.
            scan_result = None
            if getattr(settings, 'TRIVY_ENABLED', True):
                scan_result = NixpacksBuilder.scan_image(image_name)

            return {
                "image_name": image_name,
                "stdout": process.stdout or "",
                "stderr": process.stderr or "",
                "scan_result": scan_result,
            }

        except _CalledProcessError as e:
            stderr = (e.stderr or "") + "\n" + (e.stdout or "")
            # ─── Batch J: buildx default-builder self-heal ─────────
            # The default ``docker`` driver buildx builder can
            # corrupt on daemon restart. Detect that specific
            # error, create a ``docker-container`` fallback
            # builder, and re-run the build against it.
            if _is_buildx_default_broken(stderr):
                fallback_name = _buildx_fallback_builder_name()
                logger.warning(
                    "Detected broken buildx default builder in "
                    "Nixpacks stderr; creating fallback %r and "
                    "retrying the build.",
                    fallback_name,
                )
                created, fb_status = _ensure_buildx_fallback(fallback_name)
                if created:
                    logger.info(
                        "Buildx fallback ready (%s); retrying build "
                        "with BUILDX_BUILDER=%s.",
                        fb_status, fallback_name,
                    )
                    try:
                        retry = subprocess.run(
                            command,
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=DOCKER_BUILD_TIMEOUT,
                            env={
                                **os.environ,
                                "NIXPACKS_CACHE_DIR": effective_cache_dir,
                                "BUILDX_BUILDER": fallback_name,
                            },
                        )
                        # Also scan the retry-built image
                        scan_result = None
                        if getattr(settings, 'TRIVY_ENABLED', True):
                            scan_result = NixpacksBuilder.scan_image(image_name)
                        return {
                            "image_name": image_name,
                            "stdout": retry.stdout or "",
                            "stderr": retry.stderr or "",
                            "scan_result": scan_result,
                        }
                    except subprocess.CalledProcessError as retry_exc:
                        logger.error(
                            "Build retry with fallback %r also failed: %s",
                            fallback_name, retry_exc.stderr[-2000:],
                        )
                        e = retry_exc
                        stderr = (
                            (retry_exc.stderr or "")
                            + "\n[buildx fallback self-heal attempted: "
                            + fb_status + "]"
                        )
                else:
                    logger.error(
                        "Could not create buildx fallback %r: %s. "
                        "Falling back to original error.",
                        fallback_name, fb_status,
                    )
            # Capture full output for debugging
            error_detail = ""
            if e.stdout:
                error_detail += f"\n--- Build Output ---\n{e.stdout[-3000:]}"
            if e.stderr:
                error_detail += f"\n--- Build Errors ---\n{e.stderr[-3000:]}"
            logger.error(f"Build failed for {image_name}:{error_detail}")
            raise RuntimeError(f"Nixpacks build failed:\n{error_detail or e.stderr}") from e

    @staticmethod
    def push_image(
        image_name: str,
        registry_url: str,
        username: str | None = None,
        password: str | None = None,
    ) -> tuple[str, str | None]:
        """
        Tags and pushes the image to the internal or external registry.

        If the registry is unreachable (e.g. the node has no registry service),
        logs a warning and returns the original local image name so the
        deploy phase can fall back to the local image.

        Automatically runs ``docker login`` before pushing if credentials
        are provided, so no manual pre-authentication is needed.

        Arguments:
            image_name: Local image name (e.g. ``smsly/myapp:abc1234``).
            registry_url: Target registry host:port (e.g. ``registry:5000``).
            username: Optional registry username. Falls back to ``settings.REGISTRY_USER``.
            password: Optional registry password. Falls back to ``settings.REGISTRY_PASSWORD``.
        """
        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()

        # Tag format: registry:5000/image_name
        # Strip http(s):// scheme — Docker image references require host:port format.
        _tag_url = registry_url
        for _scheme in ('https://', 'http://'):
            if _tag_url.startswith(_scheme):
                _tag_url = _tag_url[len(_scheme):]
        full_tag = f"{_tag_url}/{image_name}"

        try:
            image = client.images.get(image_name)
            image.tag(full_tag)

            logger.info(f"Pushing image to {full_tag}...")

            # Use passed credentials, fall back to settings
            _user = username or settings.REGISTRY_USER or ""
            _pass = password or settings.REGISTRY_PASSWORD or ""
            has_creds = bool(_user and _pass)
            auth_config = None
            if has_creds:
                auth_config = {
                    "username": _user,
                    "password": _pass,
                }

            # Auto-login: ensure Docker daemon has credentials for this
            # registry so that both SDK push and CLI fallback work without
            # requiring manual `docker login` on the host.
            if has_creds and shutil.which('docker'):
                try:
                    _login = subprocess.run(
                        ['docker', 'login', _tag_url, '-u', _user, '--password-stdin'],
                        input=_pass, capture_output=True, text=True, timeout=15,
                    )
                    if _login.returncode != 0:
                        # SECURITY: don't log raw stderr — docker CLI can
                        # echo malformed input including password chars.
                        logger.warning("docker login to %s failed (exit code %s)", _tag_url, _login.returncode)
                        # If login fails, the push will also fail. Return early
                        # with a clear error rather than pushing without auth.
                        return image_name, f"Registry login failed for {_tag_url} — cannot push without auth"
                except Exception as login_err:
                    logger.warning("docker login to %s failed: %s", _tag_url, login_err)
                    return image_name, f"Registry login failed for {_tag_url}: {login_err}"

            # ── Push via CLI (preferred — uses stored docker login creds) ──
            if has_creds and shutil.which('docker'):
                try:
                    cli_result = subprocess.run(
                        ["docker", "push", full_tag],
                        capture_output=True, text=True, timeout=300,
                    )
                    if cli_result.returncode == 0:
                        logger.info("CLI push succeeded for %s", full_tag)
                        return full_tag, None
                    cli_error = cli_result.stderr.strip() or cli_result.stdout.strip()
                    logger.error("CLI push failed: %s", cli_error)
                    return image_name, cli_error
                except Exception as cli_err:
                    logger.warning("CLI push exception: %s", cli_err)
                    return image_name, str(cli_err)

            # ── Push via SDK (no-auth fallback) ──────────────────────────
            push_result = client.images.push(full_tag, auth_config=auth_config)
            # push() returns a generator (stream=True, default) that yields
            # status lines, or a single string (stream=False).  Consume
            # all output looking for JSON errors.
            push_failed = False
            error_msg = None
            if push_result is not None:
                if isinstance(push_result, str):
                    source = push_result.split("\n")
                else:
                    source = push_result
                for line in source:
                    if '"error"' in str(line):
                        push_failed = True
                        error_msg = str(line)
                        logger.error(f"Registry push failed (SDK): {line}")
                        break

            if not push_failed:
                return full_tag, None

            return image_name, error_msg

        except Exception as e:
            logger.warning(f"Registry push failed ({e}); keeping local image name.")
            return image_name, str(e)  # fallback to local

    @staticmethod
    def scan_image(image_name: str) -> dict[str, Any]:
        """
        Scans the image using Trivy.

        Returns a report dictionary.
        Raises ``RuntimeError`` if vulnerabilities are found at or above
        ``settings.TRIVY_FAIL_ON_SEVERITY`` (default: ``CRITICAL``).
        """
        logger.info(f"Scanning image {image_name} for vulnerabilities...")

        fail_on = getattr(settings, 'TRIVY_FAIL_ON_SEVERITY', 'CRITICAL').upper()
        severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        if fail_on not in severities:
            logger.warning(
                "Invalid TRIVY_FAIL_ON_SEVERITY=%r — falling back to CRITICAL. "
                "Accepted values: %s",
                fail_on, ', '.join(severities),
            )
            fail_on = "CRITICAL"

        # Build severity list from fail_on up to CRITICAL
        fail_idx = severities.index(fail_on)
        severity_arg = ",".join(severities[fail_idx:])

        try:
            from apps.deployments.utils import find_binary
            trivy_bin = find_binary("trivy")
            if not trivy_bin:
                raise FileNotFoundError("trivy binary not found in PATH or standard directories")
        except Exception:
            logger.warning(
                "WARNING: Trivy binary not found — image built WITHOUT "
                "security scan. Install Trivy for vulnerability scanning."
            )
            return {"status": "unscanned", "reason": "trivy_missing"}

        command = [
            trivy_bin,
            "image",
            "--insecure",
            "--scanners", "vuln",
            "--format", "json",
            "--severity", severity_arg,
            image_name
        ]


        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"trivy scan failed (rc={result.returncode}): "
                    f"{(result.stderr or '').strip()[:1000]}"
                )

            report = json.loads(result.stdout)

            # Check for vulnerabilities at or above the threshold
            fail_count = 0
            for result_item in report.get('Results', []):
                for vuln in result_item.get('Vulnerabilities', []):
                    if vuln.get('Severity', '').upper() in severities[fail_idx:]:
                        fail_count += 1

            if fail_count > 0:
                msg = (
                    f"Security Scan Failed: Found {fail_count} blocking "
                    f"vulnerabilit{'y' if fail_count == 1 else 'ies'} (at or above {fail_on})."
                )
                logger.error(msg)
                raise RuntimeError(msg)

            return report

        except FileNotFoundError:
            logger.warning(
                "WARNING: Trivy binary not found — image built WITHOUT "
                "security scan. Install Trivy for vulnerability scanning."
            )
            return {"status": "unscanned", "reason": "trivy_missing"}
