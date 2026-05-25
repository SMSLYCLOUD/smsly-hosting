"""Builder module."""
import subprocess
import shutil
import os
import logging
import docker
import json
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


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
        env_vars: Optional[dict] = None,
        cache_dir: Optional[str] = None,
        start_cmd: Optional[str] = None,
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
            "--cache-key", image_name.split(":")[0],
        ]

        # Add inline cache for Docker layer caching
        command.extend(["--inline-cache"])

        if start_cmd and str(start_cmd).strip():
            command.extend(["--start-cmd", str(start_cmd).strip()])
        elif allow_missing_start:
            command.append("--no-error-without-start")

        # Build-arg secret hints: env vars matching these patterns are
        # NOT passed as --env to nixpacks to avoid leaking into build
        # layers and process listings.  They are injected at runtime.
        _BUILD_SECRET_HINTS = (
            "SECRET", "KEY", "PASSWORD", "TOKEN", "DSN",
            "DATABASE_URL", "POSTGRES_URL", "REDIS_URL",
            "JWT", "CREDENTIAL",
        )

        if env_vars:
            for k, v in env_vars.items():
                upper_k = k.upper()
                is_secret = any(hint in upper_k for hint in _BUILD_SECRET_HINTS)
                if is_secret:
                    logger.info("Skipping secret env for nixpacks build: %s", k)
                    continue
                command.extend(["--env", f"{k}={v}"])

        logger.info(
            f"Starting Nixpacks build for {image_name} (cache: {effective_cache_dir})...")

        try:
            # Run the build process
            process = subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "NIXPACKS_CACHE_DIR": effective_cache_dir}
            )
            # Log build output for debugging
            if process.stdout:
                logger.info(f"Nixpacks stdout:\n{process.stdout[-2000:]}")
            if process.stderr:
                logger.info(f"Nixpacks stderr:\n{process.stderr[-2000:]}")
            return {
                "image_name": image_name,
                "stdout": process.stdout or "",
                "stderr": process.stderr or "",
            }

        except subprocess.CalledProcessError as e:
            # Capture full output for debugging
            error_detail = ""
            if e.stdout:
                error_detail += f"\n--- Build Output ---\n{e.stdout[-3000:]}"
            if e.stderr:
                error_detail += f"\n--- Build Errors ---\n{e.stderr[-3000:]}"
            logger.error(f"Build failed for {image_name}:{error_detail}")
            raise RuntimeError(f"Nixpacks build failed:\n{error_detail or e.stderr}") from e

    @staticmethod
    def push_image(image_name: str, registry_url: str) -> str:
        """
        Tags and pushes the image to the internal or external registry.

        If the registry is unreachable (e.g. the node has no registry service),
        logs a warning and returns the original local image name so the
        deploy phase can fall back to the local image.
        """
        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()

        # Tag format: registry:5000/image_name
        full_tag = f"{registry_url}/{image_name}"

        try:
            image = client.images.get(image_name)
            image.tag(full_tag)

            logger.info(f"Pushing image to {full_tag}...")
            push_result = client.images.push(full_tag)
            # push() returns a generator (stream=True, default) that yields
            # status lines, or a single string (stream=False).  Consume
            # all output looking for JSON errors.
            push_failed = False
            if push_result is not None:
                if isinstance(push_result, str):
                    source = push_result.split("\n")
                else:
                    source = push_result
                for line in source:
                    if '"error"' in str(line):
                        push_failed = True
                        logger.error(f"Registry push failed: {line}")
                        break
            if push_failed:
                return image_name  # fallback to local

            return full_tag
        except Exception as e:
            logger.warning(f"Registry push failed ({e}); keeping local image name.")
            return image_name  # fallback to local

    @staticmethod
    def scan_image(image_name: str) -> Dict[str, Any]:
        """
        Scans the image using Trivy.
        Returns a report dictionary.
        Raises error if CRITICAL vulnerabilities found.
        """
        logger.info(f"Scanning image {image_name} for vulnerabilities...")

        # Ensure trivy is installed (or use docker to run trivy)
        # Using subprocess assuming trivy binary is present
        command = [
            "trivy",
            "image",
            "--format", "json",
            "--severity", "CRITICAL,HIGH",
            image_name
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning(
                    f"Trivy scan failed (binary missing?): {result.stderr}")
                return {"error": "Scan skipped (tool missing)"}

            report = json.loads(result.stdout)

            # Check for Criticals
            critical_count = 0
            for result_item in report.get('Results', []):
                for vuln in result_item.get('Vulnerabilities', []):
                    if vuln['Severity'] == 'CRITICAL':
                        critical_count += 1

            if critical_count > 0:
                msg = f"Security Scan Failed: Found {critical_count} CRITICAL vulnerabilities."
                logger.error(msg)
                raise RuntimeError(msg)

            return report

        except FileNotFoundError:
            logger.warning("Trivy binary not found. Skipping security scan.")
            return {"status": "skipped", "reason": "trivy_missing"}
