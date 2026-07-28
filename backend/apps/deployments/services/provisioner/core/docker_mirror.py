import logging
import os
import subprocess

logger = logging.getLogger(__name__)

COMPOSE_FILE = os.environ.get("COMPOSE_FILE", "docker-compose.prod.yml")
INSTALL_DIR = os.environ.get("SMSLY_INSTALL_DIR", "/opt/smsly-hosting")


def _ensure_docker_mirror():
    compose_path = os.path.join(INSTALL_DIR, COMPOSE_FILE)
    if not os.path.exists(compose_path):
        return
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_path, "--profile", "build-cache",
             "up", "-d", "--no-deps", "docker-mirror"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            logger.info("Docker mirror started for provisioning")
        else:
            logger.debug("Docker mirror start skipped: %s", result.stderr[:200])
    except Exception as exc:
        logger.debug("Docker mirror start failed (non-fatal): %s", exc)


def _stop_docker_mirror():
    compose_path = os.path.join(INSTALL_DIR, COMPOSE_FILE)
    if not os.path.exists(compose_path):
        return
    try:
        subprocess.run(
            ["docker", "compose", "-f", compose_path, "stop", "docker-mirror"],
            capture_output=True, text=True, timeout=30,
        )
        logger.info("Docker mirror stopped after provisioning")
    except Exception as exc:
        logger.debug("Docker mirror stop failed (non-fatal): %s", exc)
