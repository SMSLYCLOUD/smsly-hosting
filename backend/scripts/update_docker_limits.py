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

def update_docker_limits():
    """
    Update docker limits of all running services that have vpa_enabled=True.
    This replaces hard limits with soft limits in place.
    """
    try:
        client = docker.from_env()
    except Exception as e:
        logger.error(f"Could not connect to Docker: {e}")
        return

    services = Service.objects.filter(vpa_enabled=True)
    count = 0
    for service in services:
        try:
            container = client.containers.get(service.name)

            # Prepare update kwargs for soft limits
            memory = service.memory_mb
            cpu = int(service.cpu_cores * 1024)

            update_kwargs = {}
            if memory and memory > 0:
                update_kwargs['mem_reservation'] = f"{memory}m"
                # Need to clear mem_limit if it exists. Docker API allows mem_limit=0 to remove the limit
                update_kwargs['mem_limit'] = 0

            if cpu and cpu > 0:
                update_kwargs['cpu_shares'] = max(2, int((cpu / 1000) * 1024))
                # Clear quota
                update_kwargs['cpu_quota'] = 0

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
