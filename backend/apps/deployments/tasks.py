from celery import shared_task
from django.utils import timezone
import logging
from apps.cloud.services.compute import ComputeService
from apps.cloud.services.data import DataService
from apps.cloud.services.builder import NixpacksBuilder
from apps.cloud.models import CloudProvider

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def smart_deploy_task(self, deployment_id: str, provider_id: str):
    """
    Orchestrates a deployment to any cloud provider.
    
    1. Fetch Deployment & Service details.
    2. Build Image (if Git source) using Nixpacks.
    3. Initialize ComputeService with the selected Provider.
    4. Deploy Container/Function.
    5. Update Deployment status.
    """
    from apps.deployments.models import Deployment, Service
    
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        service = deployment.service
        provider = CloudProvider.objects.get(id=provider_id)
        
        deployment.status = Deployment.Status.BUILDING
        deployment.started_at = timezone.now()
        deployment.save()

        # Step 1: Clone repository if Git source
        image_name = service.docker_image
        source_dir = None
        
        if service.deploy_type == 'GIT':
            import git
            import shutil
            import tempfile
            
            # Create temporary directory for build
            source_dir = tempfile.mkdtemp(prefix=f"build_{deployment.id}_")
            
            try:
                logger.info(f"Cloning repository: {service.repository_url} (branch: {service.branch})")
                deployment.build_logs = f"Cloning {service.repository_url}...\n"
                deployment.save()
                
                # Clone repository
                repo = git.Repo.clone_from(
                    service.repository_url,
                    source_dir,
                    branch=service.branch,
                    depth=1  # Shallow clone for faster builds
                )
                
                # Get actual commit hash
                deployment.commit_hash = repo.head.commit.hexsha
                deployment.commit_message = repo.head.commit.message
                deployment.save()
                
                logger.info(f"Cloned successfully. Commit: {deployment.commit_hash[:7]}")
                
                # Build image name
                image_name = f"smsly/{service.name}:{deployment.commit_hash[:7]}"
                
                # Build using Nixpacks
                logger.info(f"Building image with Nixpacks: {image_name}")
                deployment.build_logs += f"\nBuilding image {image_name}...\n"
                deployment.save()
                
                from apps.cloud.services.builder import NixpacksBuilder
                builder = NixpacksBuilder()
                
                # Prepare environment variables for build
                build_env_vars = {env.key: env.value for env in service.env_vars.all()}
                
                # Build the image
                built_image = builder.build_image(
                    source_dir=source_dir,
                    image_name=image_name,
                    env_vars=build_env_vars
                )
                
                deployment.build_logs += f"\n✓ Successfully built {built_image}\n"
                deployment.save()
                
            except git.GitCommandError as e:
                error_msg = f"Git clone failed: {str(e)}"
                logger.error(error_msg)
                deployment.build_logs += f"\n✗ {error_msg}\n"
                deployment.status = Deployment.Status.FAILED
                deployment.finished_at = timezone.now()
                deployment.save()
                
                # Cleanup
                if source_dir and os.path.exists(source_dir):
                    shutil.rmtree(source_dir, ignore_errors=True)
                
                raise self.retry(exc=e, countdown=30)
                
            except Exception as e:
                error_msg = f"Build failed: {str(e)}"
                logger.error(error_msg)
                deployment.build_logs += f"\n✗ {error_msg}\n"
                deployment.status = Deployment.Status.FAILED
                deployment.finished_at = timezone.now()
                deployment.save()
                
                # Cleanup
                if source_dir and os.path.exists(source_dir):
                    shutil.rmtree(source_dir, ignore_errors=True)
                
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

    except Exception as e:
        logger.error(f"Deployment {deployment_id} failed: {e}")
        deployment.status = Deployment.Status.FAILED
        deployment.finished_at = timezone.now()
        deployment.build_logs += f"\nError: {str(e)}"
        deployment.save()
        raise self.retry(exc=e, countdown=30)
