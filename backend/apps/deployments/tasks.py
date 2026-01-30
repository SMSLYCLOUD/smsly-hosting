from celery import shared_task
from django.utils import timezone
from django.conf import settings
import logging
import os
import tempfile
import shutil
import git
from apps.cloud.services.compute import ComputeService
from apps.cloud.services.data import DataService
from apps.cloud.services.builder import NixpacksBuilder
from apps.deployments.services.git import GitManager
from apps.cloud.models import CloudProvider

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def smart_deploy_task(self, deployment_id: str, provider_id: str):
    """
    Orchestrates a deployment to any cloud provider with REAL Build Pipeline.
    
    1. Clone Git Repo (if Git source).
    2. Build Image via Nixpacks.
    3. Push to Registry.
    4. Deploy Container.
    """
    from apps.deployments.models import Deployment, Service
    
    source_dir = None
    
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        service = deployment.service
        provider = CloudProvider.objects.get(id=provider_id)
        
        deployment.status = Deployment.Status.BUILDING
        deployment.started_at = timezone.now()
        deployment.save()

        # Step 1: Build Pipeline
        image_name = service.docker_image
        
        if service.deploy_type == 'GIT':
            try:
                # Create temporary build directory
                build_dir = tempfile.mkdtemp(prefix=f"build_{deployment.id}_")
                
                # A. Clone Repository
                logger.info(f"Cloning repository: {service.repository_url} (branch: {service.branch})")
                deployment.build_logs = f"Cloning {service.repository_url}...\n"
                deployment.save()
                
                source_dir = GitManager.clone_repo(
                    repo_url=service.repository_url,
                    branch=service.branch or 'main',
                    destination=build_dir
                )
                
                # Get commit hash from cloned repo
                repo = git.Repo(source_dir)
                deployment.commit_hash = repo.head.commit.hexsha
                deployment.commit_message = repo.head.commit.message
                deployment.save()
                
                logger.info(f"Cloned successfully. Commit: {deployment.commit_hash[:7]}")
                
                # B. Build with Nixpacks
                local_tag = f"smsly/{service.name}:{deployment.commit_hash[:7]}"
                logger.info(f"Building image with Nixpacks: {local_tag}")
                deployment.build_logs += f"\nBuilding image {local_tag}...\n"
                deployment.save()
                
                # Prepare environment variables for build
                build_env_vars = {env.key: env.value for env in service.env_vars.all()}
                
                # Build the image
                NixpacksBuilder.build_image(
                    source_dir=source_dir,
                    image_name=local_tag,
                    env_vars=build_env_vars
                )
                
                deployment.build_logs += f"✓ Successfully built {local_tag}\n"
                deployment.save()
                
                # C. Push to Registry (if configured)
                registry_url = getattr(settings, 'CONTAINER_REGISTRY_URL', None)
                if registry_url:
                    logger.info(f"Pushing image to registry: {registry_url}")
                    deployment.build_logs += f"\nPushing to {registry_url}...\n"
                    deployment.save()
                    
                    remote_tag = NixpacksBuilder.push_image(local_tag, registry_url)
                    image_name = remote_tag  # Use registry image for deployment
                    
                    deployment.build_logs += f"✓ Pushed to {remote_tag}\n"
                    deployment.save()
                else:
                    # Use local image if no registry configured
                    image_name = local_tag
                    logger.info("No registry configured, using local image")
                
            except Exception as e:
                error_msg = f"Build pipeline failed: {str(e)}"
                logger.error(error_msg)
                deployment.build_logs += f"\n✗ {error_msg}\n"
                deployment.status = Deployment.Status.FAILED
                deployment.finished_at = timezone.now()
                deployment.save()
                
                # Cleanup on build failure
                if source_dir and os.path.exists(source_dir):
                    shutil.rmtree(source_dir, ignore_errors=True)
                    logger.info(f"Cleaned up build directory after failure: {source_dir}")
                
                raise self.retry(exc=e, countdown=30)

        # Step 2: Deploy
        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()

        compute = ComputeService(provider)
        
        # Prepare Env Vars
        env_vars = {env.key: env.value for env in service.env_vars.all()}
        
        # Call Universal Adapter
        resource = compute.deploy_container(
            name=service.name,
            image=image_name,
            env_vars=env_vars,
            cpu=int(service.cpu_cores * 1024),
            memory=service.memory_mb
        )
        
        # Step 3: Success
        deployment.status = Deployment.Status.ACTIVE
        deployment.finished_at = timezone.now()
        deployment.container_id = resource.resource_id
        deployment.save()
        
        logger.info(f"Deployment {deployment_id} successful on {provider.name}")
        
        # Cleanup temporary build directory on success
        if source_dir and os.path.exists(source_dir):
            shutil.rmtree(source_dir, ignore_errors=True)
            logger.info(f"Cleaned up build directory: {source_dir}")

    except Exception as e:
        logger.error(f"Deployment {deployment_id} failed: {e}")
        deployment.status = Deployment.Status.FAILED
        deployment.finished_at = timezone.now()
        deployment.save()
        
        # Cleanup on failure
        if source_dir and os.path.exists(source_dir):
            shutil.rmtree(source_dir, ignore_errors=True)
            logger.info(f"Cleaned up build directory after failure: {source_dir}")
        
        raise self.retry(exc=e, countdown=30)


@shared_task(bind=True, max_retries=3)
def provision_addon_task(self, addon_id: str):
    """
    Provisions an addon (database, cache, etc.) on the cloud provider.
    """
    from apps.deployments.models_addons import Addon
    
    try:
        addon = Addon.objects.get(id=addon_id)
        addon.status = Addon.Status.PROVISIONING
        addon.save()
        
        logger.info(f"Provisioning addon: {addon.name} ({addon.addon_type})")
        
        # Get the cloud provider's data service
        provider = addon.service.provider if addon.service else None
        if not provider:
            raise ValueError("No cloud provider associated with addon")
        
        data_service = DataService(provider)
        
        # Provision based on addon type
        if addon.addon_type == 'POSTGRES':
            resource = data_service.provision_database(
                name=addon.name,
                engine='postgres',
                size='db.t3.micro'
            )
            addon.connection_string = resource.get('connection_string', '')
            
        elif addon.addon_type == 'REDIS':
            resource = data_service.provision_cache(
                name=addon.name,
                engine='redis',
                size='cache.t3.micro'
            )
            addon.connection_string = resource.get('connection_string', '')
            
        elif addon.addon_type == 'MYSQL':
            resource = data_service.provision_database(
                name=addon.name,
                engine='mysql',
                size='db.t3.micro'
            )
            addon.connection_string = resource.get('connection_string', '')
        
        else:
            raise ValueError(f"Unsupported addon type: {addon.addon_type}")
        
        addon.status = Addon.Status.ACTIVE
        addon.save()
        
        logger.info(f"Addon {addon.name} provisioned successfully")
        
    except Exception as e:
        logger.error(f"Failed to provision addon {addon_id}: {e}")
        addon.status = Addon.Status.FAILED
        addon.save()
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, max_retries=3)
def deprovision_addon_task(self, addon_id: str):
    """
    Deprovisions (deletes) an addon from the cloud provider.
    """
    from apps.deployments.models_addons import Addon
    
    try:
        addon = Addon.objects.get(id=addon_id)
        addon.status = Addon.Status.DEPROVISIONING
        addon.save()
        
        logger.info(f"Deprovisioning addon: {addon.name}")
        
        provider = addon.service.provider if addon.service else None
        if not provider:
            logger.warning(f"No provider for addon {addon.name}, marking as deleted")
            addon.delete()
            return
        
        data_service = DataService(provider)
        
        # Deprovision based on addon type
        if addon.addon_type in ['POSTGRES', 'MYSQL']:
            data_service.delete_database(addon.name)
        elif addon.addon_type == 'REDIS':
            data_service.delete_cache(addon.name)
        
        # Delete the addon record
        addon.delete()
        logger.info(f"Addon {addon.name} deprovisioned successfully")
        
    except Exception as e:
        logger.error(f"Failed to deprovision addon {addon_id}: {e}")
        raise self.retry(exc=e, countdown=60)

