import subprocess
import os
import logging
import docker
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
            # Run the build process
            process = subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            logger.info(f"Build successful: {process.stdout}")
            return image_name

        except subprocess.CalledProcessError as e:
            logger.error(f"Build failed: {e.stderr}")
            raise RuntimeError(f"Nixpacks build failed: {e.stderr}")

    @staticmethod
    def push_image(image_name: str, registry_url: str) -> str:
        """
        Tags and pushes the image to the internal or external registry.
        """
        client = docker.from_env()

        # Tag format: registry:5000/image_name
        full_tag = f"{registry_url}/{image_name}"

        try:
            image = client.images.get(image_name)
            image.tag(full_tag)

            logger.info(f"Pushing image to {full_tag}...")
            client.images.push(full_tag)

            return full_tag
        except Exception as e:
            logger.error(f"Failed to push image: {e}")
            raise
