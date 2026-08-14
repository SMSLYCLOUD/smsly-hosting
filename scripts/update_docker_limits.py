import logging
import os
import sys

import django
import docker

# Setup Django environment
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.deployments.models import Service  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _vpa_ceiling() -> float:
    """Hard ceiling multiplier for VPA-enabled containers (default 1.5x reservation)."""
    try:
        value = float(os.environ.get("VPA_CEILING_MULTIPLIER", "1.5"))
    except (TypeError, ValueError):
        value = 1.5
    return max(1.0, value)


def update_docker_limits():
    """
    Update docker limits of all running services that have vpa_enabled=True.
    Applies soft reservation + a hard ceiling so VPA services cannot starve neighbors.
    """
    try:
        client = docker.from_env()
    except Exception as e:
        logger.error(f"Could not connect to Docker: {e}")
        return

    ceiling = _vpa_ceiling()
    services = Service.objects.filter(vpa_enabled=True)
    count = 0
    for service in services:
        try:
            container = client.containers.get(service.name)

            memory = service.memory_mb
            cpu = int(service.cpu_cores * 1024)

            update_kwargs = {}
            if memory and memory > 0:
                update_kwargs['mem_reservation'] = f"{memory}m"
                update_kwargs['mem_limit'] = f"{int(memory * ceiling)}m"

            if cpu and cpu > 0:
                update_kwargs['cpu_shares'] = max(2, int((cpu / 1000) * 1024))
                update_kwargs['cpu_period'] = 100000
                update_kwargs['cpu_quota'] = int((cpu / 1000) * 100000 * ceiling)

            logger.info(f"Updating container {service.name} with {update_kwargs}")
            container.update(**update_kwargs)
            count += 1

        except docker.errors.NotFound:
            logger.info(f"Container {service.name} not found, skipping.")
        except Exception as e:
            logger.error(f"Error updating container {service.name}: {e}")

    logger.info(f"Updated {count} containers successfully.")


if __name__ == "__main__":
    update_docker_limits()
