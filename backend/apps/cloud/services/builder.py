import subprocess
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class NixpacksBuilder:
    """
    Wrapper around Nixpacks CLI to build container images from source.
    """

    @staticmethod
    def build_image(source_dir: str, image_name: str, env_vars: Optional[dict] = None) -> str:
        """
        Builds a Docker image using Nixpacks.
        Returns the image tag upon success.
        """
        if not os.path.exists(source_dir):
            raise FileNotFoundError(f"Source directory {source_dir} not found")

        command = [
            "nixpacks",
            "build",
            source_dir,
            "--name", image_name,
            "--verbose"
        ]

        if env_vars:
            for k, v in env_vars.items():
                command.extend(["--env", f"{k}={v}"])

        logger.info(f"Starting Nixpacks build for {image_name}...")

        try:
            # Run the build process with timeout
            process = subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600  # 10 minute timeout
            )
            logger.info(f"Build successful for {image_name}")
            logger.debug(f"Build output: {process.stdout}")
            return image_name

        except subprocess.TimeoutExpired:
            error_msg = f"Build timed out after 10 minutes for {image_name}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        except subprocess.CalledProcessError as e:
            error_msg = f"Nixpacks build failed: {e.stderr}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    @staticmethod
    def generate_plan(source_dir: str) -> str:
        """
        Generates a build plan (JSON) without building.
        Useful for inspecting what Nixpacks detected.
        """
        command = ["nixpacks", "plan", source_dir, "--json"]
        result = subprocess.run(command, capture_output=True, text=True)
        return result.stdout
