"""Tasks module."""
from celery import shared_task
from django.utils import timezone
from django.conf import settings
import logging
import os
import tempfile
import shutil
import git
from apps.cloud.services.compute import ComputeService
from apps.cloud.services.builder import NixpacksBuilder
from apps.deployments.services.git import GitManager
from apps.cloud.models import CloudProvider

logger = logging.getLogger(__name__)


# ==============================================================================
# Real-time log broadcasting helper
# ==============================================================================

def _broadcast_log(deployment, log_line):
    """
    Append log line to deployment and broadcast via WebSocket channel layer.
    Safe to call from sync Celery tasks.
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'build_log',
                    'log': log_line,
                    'status': deployment.status,
                    'timestamp': timezone.now().isoformat(),
                }
            )
    except Exception as e:
        # Never fail a deployment because of a log broadcast error
        logger.debug("Failed to broadcast log: %s", e)


def _broadcast_status(deployment):
    """Broadcast deployment status change via WebSocket."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'status_change',
                    'status': deployment.status,
                    'finished_at': (
                        deployment.finished_at.isoformat()
                        if deployment.finished_at else ''
                    ),
                    'duration_seconds': deployment.duration_seconds,
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast status: %s", e)


# ==============================================================================
# Smart Multi-Cloud Deployment
# ==============================================================================


@shared_task(
    bind=True,
    max_retries=3,
    soft_time_limit=540,   # Graceful timeout at 9 minutes
    time_limit=660,        # Hard kill at 11 minutes
)
def smart_deploy_task(self, deployment_id: str, provider_id: str):
    """
    Orchestrates a deployment to any cloud provider with REAL Build Pipeline.

    1. Clone Git Repo (if Git source).
    2. Build Image via Nixpacks.
    3. Push to Registry.
    4. Deploy Container.

    Broadcasts build logs in real-time via WebSocket channel layer.
    """
    from apps.deployments.models import Deployment, Service

    source_dir = None
    deployment = None

    try:
        deployment = Deployment.objects.get(id=deployment_id)
        service = deployment.service
        provider = CloudProvider.objects.get(id=provider_id)

        deployment.status = Deployment.Status.BUILDING
        deployment.started_at = timezone.now()
        deployment.save()
        _broadcast_status(deployment)

        # Step 1: Build Pipeline
        image_name = service.docker_image

        if service.deploy_type == 'GIT':
            try:
                # Create temporary build directory
                build_dir = tempfile.mkdtemp(
                    prefix=f"build_{deployment.id}_")

                # A. Clone Repository
                log_line = f"Cloning {service.repository_url}...\n"
                logger.info(
                    "Cloning repository: %s (branch: %s)",
                    service.repository_url, service.branch)
                deployment.build_logs = log_line
                deployment.save()
                _broadcast_log(deployment, log_line)

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

                log_line = (
                    f"✓ Cloned successfully. "
                    f"Commit: {deployment.commit_hash[:7]}\n")
                deployment.build_logs += log_line
                deployment.save()
                _broadcast_log(deployment, log_line)

                # B. Build with Nixpacks
                local_tag = (
                    f"smsly/{service.name}:"
                    f"{deployment.commit_hash[:7]}")
                log_line = f"\nBuilding image {local_tag}...\n"
                logger.info("Building image with Nixpacks: %s", local_tag)
                deployment.build_logs += log_line
                deployment.save()
                _broadcast_log(deployment, log_line)

                # Prepare environment variables for build
                build_env_vars = {
                    env.key: env.value
                    for env in service.env_vars.all()
                }

                # Build the image
                NixpacksBuilder.build_image(
                    source_dir=source_dir,
                    image_name=local_tag,
                    env_vars=build_env_vars
                )

                log_line = f"✓ Successfully built {local_tag}\n"
                deployment.build_logs += log_line
                deployment.save()
                _broadcast_log(deployment, log_line)

                # C. Push to Registry (if configured)
                registry_url = getattr(
                    settings, 'CONTAINER_REGISTRY_URL', None)
                if registry_url:
                    log_line = f"\nPushing to {registry_url}...\n"
                    logger.info(
                        "Pushing image to registry: %s", registry_url)
                    deployment.build_logs += log_line
                    deployment.save()
                    _broadcast_log(deployment, log_line)

                    remote_tag = NixpacksBuilder.push_image(
                        local_tag, registry_url)
                    image_name = remote_tag

                    log_line = f"✓ Pushed to {remote_tag}\n"
                    deployment.build_logs += log_line
                    deployment.save()
                    _broadcast_log(deployment, log_line)
                else:
                    # Use local image if no registry configured
                    image_name = local_tag
                    logger.info(
                        "No registry configured, using local image")

            except Exception as e:
                error_msg = f"Build pipeline failed: {str(e)}"
                logger.error(error_msg)
                log_line = f"\n✗ {error_msg}\n"
                deployment.build_logs += log_line
                deployment.status = Deployment.Status.FAILED
                deployment.finished_at = timezone.now()
                deployment.save()
                _broadcast_log(deployment, log_line)
                _broadcast_status(deployment)

                # Cleanup on build failure
                if source_dir and os.path.exists(source_dir):
                    shutil.rmtree(source_dir, ignore_errors=True)
                    logger.info(
                        "Cleaned up build directory after failure: %s",
                        source_dir)

                raise self.retry(exc=e, countdown=30)

        # Step 2: Deploy
        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()
        _broadcast_status(deployment)

        log_line = "\nDeploying container...\n"
        deployment.build_logs += log_line
        deployment.save()
        _broadcast_log(deployment, log_line)

        compute = ComputeService(provider)

        # Prepare Env Vars
        env_vars = {env.key: env.value for env in service.env_vars.all()}

        # Normalize replicas to a safe value for runtime deployment.
        requested_replicas = service.min_replicas
        try:
            replicas = int(requested_replicas)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid replicas value for service %s: %r. Falling back to 1.",
                service.name, requested_replicas
            )
            replicas = 1
        if replicas < 1:
            logger.warning(
                "Replicas must be >= 1 for service %s. Received %s; using 1.",
                service.name, replicas
            )
            replicas = 1

        # Call Universal Adapter
        resource = compute.deploy_container(
            name=service.name,
            image=image_name,
            env_vars=env_vars,
            cpu=int(service.cpu_cores * 1024),
            memory=service.memory_mb,
            replicas=replicas
        )

        # Step 3: Success
        deployment.status = Deployment.Status.ACTIVE
        deployment.finished_at = timezone.now()
        deployment.container_id = resource.resource_id
        deployment.save()

        log_line = (
            f"✓ Deployment successful! Container: "
            f"{resource.resource_id[:12]}\n"
            f"  Duration: {deployment.duration_seconds:.1f}s\n")
        deployment.build_logs += log_line
        deployment.save()
        _broadcast_log(deployment, log_line)
        _broadcast_status(deployment)

        logger.info(
            "Deployment %s successful on %s",
            deployment_id, provider.name)

        # Cleanup temporary build directory on success
        if source_dir and os.path.exists(source_dir):
            shutil.rmtree(source_dir, ignore_errors=True)
            logger.info("Cleaned up build directory: %s", source_dir)

    except Exception as e:
        logger.error("Deployment %s failed: %s", deployment_id, e)
        if deployment is not None:
            deployment.status = Deployment.Status.FAILED
            deployment.finished_at = timezone.now()
            deployment.save()
            _broadcast_status(deployment)

        # Cleanup on failure
        if source_dir and os.path.exists(source_dir):
            shutil.rmtree(source_dir, ignore_errors=True)
            logger.info(
                "Cleaned up build directory after failure: %s",
                source_dir)

        raise self.retry(exc=e, countdown=30)

# ==============================================================================
# One-Click Template Deploy (Addons + Deploy Orchestration)
# ==============================================================================


@shared_task(bind=True, max_retries=0)
def one_click_deploy_template_task(self, service_id: str, template_id: str):
    """
    Background orchestration for template deployments.

    Why this exists:
    - Addon provisioning injects env vars into the Service record.
      Those env vars MUST exist before the container is launched, otherwise the
      running container will never see the injected values.
    - This task provisions required addons first, then triggers the deployment.
    """
    from apps.deployments.models import Service, Deployment, EnvironmentVariable
    from apps.deployments.models_addons import Addon
    from services.addon_provisioner import addon_provisioner

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        logger.error("one_click_deploy_template_task: service not found: %s", service_id)
        return None

    # Load template definition from fixtures
    try:
        import json
        template_path = os.path.join(
            settings.BASE_DIR, 'apps/deployments/fixtures/templates.json'
        )
        with open(template_path, 'r', encoding='utf-8') as f:
            templates = json.load(f)
        template = next((t for t in templates if t.get('id') == template_id), None)
    except Exception as e:
        logger.error("one_click_deploy_template_task: failed to load template %s: %s", template_id, e)
        template = None

    required_addons = []
    if isinstance(template, dict):
        ra = template.get('required_addons') or []
        if isinstance(ra, list):
            required_addons = [str(x) for x in ra if x]

    # Provision required addons synchronously so env vars are ready before deploy.
    env_key_map = {
        Addon.Type.POSTGRES: 'DATABASE_URL',
        Addon.Type.REDIS: 'REDIS_URL',
        Addon.Type.MYSQL: 'MYSQL_URL',
        Addon.Type.MONGODB: 'MONGODB_URI',
    }

    for addon_type in required_addons:
        if addon_type not in (a[0] for a in Addon.Type.choices):
            logger.warning("Skipping unsupported addon type %s for service %s", addon_type, service.id)
            continue

        addon = Addon.objects.create(
            service=service,
            name=f"{addon_type.lower()}-{service.name}"[:255],
            addon_type=addon_type,
            status=Addon.Status.PROVISIONING,
        )

        try:
            container_id, connection_url = addon_provisioner.provision(addon)
            addon.connection_url = connection_url
            addon.status = Addon.Status.ACTIVE
            addon.coolify_uuid = container_id
            addon.save()

            env_key = env_key_map.get(addon.addon_type, f"{addon.addon_type}_URL")
            EnvironmentVariable.objects.update_or_create(
                service=service,
                key=env_key,
                defaults={'value': connection_url, 'is_secret': True},
            )
        except Exception as e:
            addon.status = Addon.Status.FAILED
            addon.save()
            logger.error("Addon provisioning failed (%s) for service %s: %s", addon_type, service.id, e)
            # Fail-closed: do not deploy a template that is missing required deps.
            return None

    # Trigger deployment after deps are ready.
    provider = service.provider if service.provider and service.provider.is_active else None
    if not provider:
        provider = CloudProvider.objects.filter(is_active=True).first()
    if not provider:
        logger.error("one_click_deploy_template_task: no active provider configured for service %s", service.id)
        return None

    deployment = Deployment.objects.create(
        service=service,
        status=Deployment.Status.QUEUED,
        commit_hash='template',
        commit_message=f"Template Deploy: {template_id}",
    )
    smart_deploy_task.delay(str(deployment.id), str(provider.id))
    return str(deployment.id)

# ==============================================================================
# LEGACY: Addon Provisioning (Docker-native)
# ==============================================================================


@shared_task(bind=True, max_retries=3)
def provision_addon_task(self, addon_id: str):
    """
    Provision a database addon using Docker containers.
    """
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
        logger.info(
            "Provisioning addon %s (%s)",
            addon.name, addon.addon_type)

        # Create container via Docker
        container_id, connection_url = addon_provisioner.provision(addon)

        addon.connection_url = connection_url
        addon.status = Addon.Status.ACTIVE
        addon.coolify_uuid = container_id
        addon.save()

        # Inject connection URL if attached to a service
        if addon.service:
            env_key = ENV_KEY_MAP.get(
                addon.addon_type, f"{addon.addon_type}_URL")
            EnvironmentVariable.objects.update_or_create(
                service=addon.service,
                key=env_key,
                defaults={'value': connection_url, 'is_secret': True}
            )

        logger.info("Addon %s provisioned successfully", addon.name)

    except Exception as e:
        logger.error("Failed to provision addon %s: %s", addon_id, e)
        raise self.retry(exc=e, countdown=30)


@shared_task
def deprovision_addon_task(addon_id: str):
    """Delete addon container."""
    from apps.deployments.models_addons import Addon
    from apps.deployments.models import EnvironmentVariable
    from services.addon_provisioner import addon_provisioner

    try:
        addon = Addon.objects.get(id=addon_id)
        if addon.coolify_uuid:
            addon_provisioner.deprovision(
                addon.coolify_uuid, f"addon-{addon.id}")

        addon.status = Addon.Status.DELETED
        addon.save()
    except Exception as e:
        logger.error("Failed to deprovision: %s", e)


@shared_task(bind=True, max_retries=3)
def backup_addon_task(self, addon_id: str):
    """
    Create a backup for the specified addon.
    """
    from apps.deployments.models_addons import Addon, Backup
    from services.addon_provisioner import addon_provisioner
    
    try:
        addon = Addon.objects.get(id=addon_id)
        logger.info(f"Starting backup for {addon.name}")
        
        # Create pending backup record
        backup = Backup.objects.create(
            addon=addon,
            status=Backup.Status.PENDING
        )
        
        try:
            # Execute backup
            file_path = addon_provisioner.create_backup(addon)
            
            # Update record
            backup.file_path = file_path
            if os.path.exists(file_path):
                backup.size_bytes = os.path.getsize(file_path)
            backup.status = Backup.Status.COMPLETED
            backup.completed_at = timezone.now()
            backup.save()
            
            logger.info(f"Backup {backup.id} created for {addon.name} at {file_path}")
            return str(backup.id)
            
        except Exception as e:
            backup.status = Backup.Status.FAILED
            backup.error_message = str(e)
            backup.save()
            raise e
            
    except Exception as e:
        logger.error(f"Backup task failed for {addon_id}: {e}")
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True)
def restore_addon_task(self, backup_id: str):
    """
    Restore a backup to the addon.
    WARNING: This overwrites current data.
    """
    from apps.deployments.models_addons import Backup
    from services.addon_provisioner import addon_provisioner
    
    try:
        backup = Backup.objects.get(id=backup_id)
        addon = backup.addon
        
        logger.info(f"Restoring backup {backup.id} to {addon.name}")
        
        addon_provisioner.restore_backup(addon, backup.file_path)
        
        logger.info(f"Restore complete for {addon.name}")
        return True
        
    except Exception as e:
        logger.error(f"Restore task failed for {backup_id}: {e}")
        raise e
