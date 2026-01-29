from celery import shared_task
from services.orchestrator import Orchestrator
import logging

logger = logging.getLogger(__name__)


@shared_task
def run_deployment_task(deployment_id):
    orchestrator = Orchestrator(deployment_id)
    orchestrator.run_deployment()


@shared_task(bind=True, max_retries=3)
def provision_addon_task(self, addon_id: str):
    """
    Provision a database addon using Docker containers.
    
    1. Create database container via Docker
    2. Get connection URL
    3. Inject as environment variable into the service
    """
    from apps.deployments.models_addons import Addon
    from apps.deployments.models import EnvironmentVariable
    from services.addon_provisioner import addon_provisioner
    
    # Environment variable key mapping
    ENV_KEY_MAP = {
        Addon.Type.POSTGRES: 'DATABASE_URL',
        Addon.Type.REDIS: 'REDIS_URL',
        Addon.Type.MYSQL: 'MYSQL_URL',
        Addon.Type.MONGODB: 'MONGODB_URI',
    }
    
    try:
        addon = Addon.objects.get(id=addon_id)
        logger.info(f"Provisioning addon {addon.name} ({addon.addon_type}) for {addon.service.name}")
        
        # Create container via Docker
        container_id, connection_url = addon_provisioner.provision(addon)
        
        # Update addon record
        addon.connection_url = connection_url
        addon.status = Addon.Status.ACTIVE
        # Store container info for later cleanup (reusing coolify_uuid field as container_id)
        addon.coolify_uuid = container_id
        addon.save()
        
        # Inject connection URL as environment variable
        env_key = ENV_KEY_MAP.get(addon.addon_type, f"{addon.addon_type}_URL")
        EnvironmentVariable.objects.update_or_create(
            service=addon.service,
            key=env_key,
            defaults={
                'value': connection_url,
                'is_secret': True,
            }
        )
        
        logger.info(f"Addon {addon.name} provisioned successfully: {container_id}")
        
    except Addon.DoesNotExist:
        logger.error(f"Addon {addon_id} not found")
    except Exception as e:
        logger.error(f"Failed to provision addon {addon_id}: {e}")
        try:
            addon = Addon.objects.get(id=addon_id)
            addon.status = Addon.Status.FAILED
            addon.save()
        except:
            pass
        # Retry on failure
        raise self.retry(exc=e, countdown=30)


@shared_task
def deprovision_addon_task(addon_id: str):
    """Delete addon container and mark as deleted."""
    from apps.deployments.models_addons import Addon
    from apps.deployments.models import EnvironmentVariable
    from services.addon_provisioner import addon_provisioner
    
    ENV_KEY_MAP = {
        Addon.Type.POSTGRES: 'DATABASE_URL',
        Addon.Type.REDIS: 'REDIS_URL',
        Addon.Type.MYSQL: 'MYSQL_URL',
        Addon.Type.MONGODB: 'MONGODB_URI',
    }
    
    try:
        addon = Addon.objects.get(id=addon_id)
        
        # Get container info
        container_id = addon.coolify_uuid  # We stored container_id here
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        
        if container_id:
            # Remove Docker container
            addon_provisioner.deprovision(container_id, container_name)
        
        # Remove associated env var
        env_key = ENV_KEY_MAP.get(addon.addon_type)
        if env_key:
            EnvironmentVariable.objects.filter(
                service=addon.service,
                key=env_key
            ).delete()
        
        # Mark as deleted
        addon.status = Addon.Status.DELETED
        addon.save()
        
        logger.info(f"Addon {addon.name} deprovisioned successfully")
        
    except Addon.DoesNotExist:
        logger.error(f"Addon {addon_id} not found")
    except Exception as e:
        logger.error(f"Failed to deprovision addon {addon_id}: {e}")
