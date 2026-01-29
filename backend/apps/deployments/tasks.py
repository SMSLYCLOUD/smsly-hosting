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
    Provision a database addon via Coolify API.
    
    1. Create database in Coolify
    2. Get connection URL
    3. Inject as environment variable into the service
    """
    from .models_addons import Addon
    from .models import EnvironmentVariable
    from services.coolify_client import coolify_client
    
    # Type map for Coolify
    ADDON_TYPE_MAP = {
        Addon.Type.POSTGRES: 'postgresql',
        Addon.Type.REDIS: 'redis',
        Addon.Type.MYSQL: 'mysql',
        Addon.Type.MONGODB: 'mongodb',
    }
    
    ENV_KEY_MAP = {
        Addon.Type.POSTGRES: 'DATABASE_URL',
        Addon.Type.REDIS: 'REDIS_URL',
        Addon.Type.MYSQL: 'MYSQL_URL',
        Addon.Type.MONGODB: 'MONGODB_URI',
    }
    
    try:
        addon = Addon.objects.get(id=addon_id)
        logger.info(f"Provisioning addon {addon.name} ({addon.addon_type}) for {addon.service.name}")
        
        # Create database in Coolify
        coolify_type = ADDON_TYPE_MAP.get(addon.addon_type)
        if not coolify_type:
            logger.error(f"Unknown addon type: {addon.addon_type}")
            addon.status = Addon.Status.FAILED
            addon.save()
            return
        
        result = coolify_client.create_database_sync(
            name=f"{addon.service.name}-{addon.name}",
            db_type=coolify_type,
        )
        
        # Extract connection URL and UUID from Coolify response
        coolify_uuid = result.get('uuid')
        connection_url = result.get('internal_db_url') or result.get('connection_url', '')
        
        if not coolify_uuid:
            raise ValueError("No UUID returned from Coolify")
        
        # Update addon record
        addon.coolify_uuid = coolify_uuid
        addon.connection_url = connection_url
        addon.status = Addon.Status.ACTIVE
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
        
        logger.info(f"Addon {addon.name} provisioned successfully: {coolify_uuid}")
        
        # If service has a Coolify app, update its env vars too
        if addon.service.coolify_uuid:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                loop.run_until_complete(
                    coolify_client.set_environment_variables(
                        addon.service.coolify_uuid,
                        [{"key": env_key, "value": connection_url, "is_secret": True}]
                    )
                )
                loop.close()
            except Exception as e:
                logger.warning(f"Failed to update Coolify app env vars: {e}")
        
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
    """Delete addon from Coolify and mark as deleted."""
    from .models_addons import Addon
    from .models import EnvironmentVariable
    from services.coolify_client import coolify_client
    import asyncio
    
    ENV_KEY_MAP = {
        Addon.Type.POSTGRES: 'DATABASE_URL',
        Addon.Type.REDIS: 'REDIS_URL',
        Addon.Type.MYSQL: 'MYSQL_URL',
        Addon.Type.MONGODB: 'MONGODB_URI',
    }
    
    try:
        addon = Addon.objects.get(id=addon_id)
        
        if addon.coolify_uuid:
            # Delete from Coolify
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    coolify_client.delete_database(addon.coolify_uuid)
                )
            finally:
                loop.close()
        
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

