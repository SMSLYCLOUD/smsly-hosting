"""Tasks module."""
import logging
import shutil
import tempfile
import subprocess
import os
import json
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.cloud.services.builder import NixpacksBuilder
from apps.cloud.services.compute import ComputeService
from apps.cloud.services.function_provisioner import FunctionProvisioner
from apps.deployments.services.pipeline import PipelineManager, PipelineError
from apps.deployments.models import Service, Deployment, EnvironmentVariable
from apps.deployments.models_addons import Addon, Backup
from apps.deployments.models_storage import Volume
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    update_stage,
)
from services.addon_provisioner import addon_provisioner

logger = logging.getLogger(__name__)

# AI diagnosis task — imported at top level to avoid circular import issues
try:
    from apps.deployments.tasks_ai import analyze_failure_task
except ImportError:
    analyze_failure_task = None


@shared_task(
    bind=True,
    max_retries=3,
    soft_time_limit=7200,  # 2 hours (heavy deps: torch, playwright, transformers)
    time_limit=7500,       # 2h 5m hard kill
)
def smart_deploy_task(self, deployment_id: str, provider_id: str):
    """
    Orchestrates a deployment using PipelineManager for build steps.

    For fresh GIT deploys: runs analysis only, pauses at REVIEW status.
    For rollbacks, restarts, and non-GIT: runs full pipeline immediately.
    """
    # pylint: disable=too-many-locals
    deployment = None
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s cancelled before start", deployment_id)
            return

        service = deployment.service
        provider = CloudProvider.objects.get(id=provider_id)

        # 1. Build Phase (Pipeline)
        if service.deploy_type == 'GIT':
            manager = PipelineManager(deployment)

            # Rollbacks/restarts → full pipeline (skip review)
            if deployment.is_rollback:
                image_name = manager.run()
            else:
                # Fresh deploy → analysis only, pause for review
                manager.run_analysis_only()
                broadcast_status(deployment)
                return  # Paused at REVIEW — user must approve

        elif service.deploy_type == 'FUNCTION':
            image_name = _build_function(deployment, service)

        elif service.deploy_type == 'DOCKER':
            image_name = service.docker_image

        else:
            raise ValueError(f"Unsupported deploy type: {service.deploy_type}")

        # 2. Deploy Phase (only reached for rollbacks/non-GIT)
        _deploy_container(deployment, provider, image_name)

    except PipelineError as e:
        _handle_failure(self, deployment, str(e), "Pipeline Failure")
    except Exception as e: # pylint: disable=broad-exception-caught
        _handle_failure(self, deployment, str(e), "System Failure")


@shared_task(
    bind=True,
    max_retries=2,
    soft_time_limit=7200,
    time_limit=7500,
)
def resume_deploy_task(self, deployment_id: str, provider_id: str):
    """
    Phase 2: Build + Deploy after user approves review.
    Called when user hits POST /api/v1/deployments/{id}/approve/.
    """
    deployment = None
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s cancelled", deployment_id)
            return

        service = deployment.service
        provider = CloudProvider.objects.get(id=provider_id)

        # Build phase
        manager = PipelineManager(deployment)
        image_name = manager.run_build_only()

        # Deploy phase
        _deploy_container(deployment, provider, image_name)

    except PipelineError as e:
        _handle_failure(self, deployment, str(e), "Build Failure")
    except Exception as e:  # pylint: disable=broad-exception-caught
        _handle_failure(self, deployment, str(e), "System Failure")


def _build_function(deployment, service) -> str:
    """Build serverless function image."""
    build_dir = None
    try:
        deployment.status = 'BUILDING'
        deployment.save()
        broadcast_status(deployment)

        build_dir = tempfile.mkdtemp(prefix=f"func_{deployment.id}_")
        FunctionProvisioner.prepare_context(service, build_dir)

        tag = f"smsly/func-{service.name}:{deployment.id[:7]}"

        append_log(deployment, f"Building function {tag}...\n")

        cmd = ["docker", "build", "-t", tag, build_dir]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)

        registry = getattr(settings, 'CONTAINER_REGISTRY_URL', None)
        if registry:
            return NixpacksBuilder.push_image(tag, registry)
        return tag

    finally:
        if build_dir:
            shutil.rmtree(build_dir, ignore_errors=True)


def _deploy_container(deployment, provider, image_name):
    """Deploy the built image to the cloud provider."""
    # pylint: disable=too-many-locals, R0914
    update_stage(deployment, 'Deploy', 'running')
    start = timezone.now()

    try:
        service = deployment.service
        compute = ComputeService(provider)

        env_vars = {env.key: env.value for env in service.env_vars.all()}
        env_vars.setdefault('PORT', '8000')
        if service.public_domain:
            env_vars.setdefault('PUBLIC_DOMAIN', service.public_domain)

        # Inject addon connection URLs into deployed container
        from apps.deployments.models_addons import Addon
        from services.addon_provisioner import AddonProvisioner
        for addon in Addon.objects.filter(service=service, status='ACTIVE'):
            env_key = AddonProvisioner.ENV_KEY_MAP.get(addon.addon_type)
            if env_key and addon.connection_url:
                env_vars.setdefault(env_key, addon.connection_url)
                # Qdrant: also set host/port for apps that expect QDRANT_HOST
                if addon.addon_type == 'QDRANT':
                    from urllib.parse import urlparse
                    parsed = urlparse(addon.connection_url)
                    env_vars.setdefault('QDRANT_HOST', parsed.hostname or 'localhost')
                    env_vars.setdefault('QDRANT_PORT', str(parsed.port or 6333))

        volumes = [{'name': v.name, 'mount_path': v.mount_path}
                   for v in Volume.objects.filter(service=service)]

        healthcheck = None
        if service.health_check_path:
            healthcheck = {
                'path': service.health_check_path,
                'interval': service.health_check_interval,
                'timeout': service.health_check_timeout,
                'retries': service.health_check_retries
            }

        resource = compute.deploy_container(
            name=service.name,
            image=image_name,
            env_vars=env_vars,
            cpu=int(service.cpu_cores * 1024),
            memory=service.memory_mb,
            replicas=service.min_replicas,
            volumes=volumes,
            healthcheck=healthcheck,
            restart_policy=service.restart_policy
        )

        deployment.status = 'ACTIVE'
        deployment.container_id = resource.resource_id
        deployment.finished_at = timezone.now()
        deployment.save()

        update_stage(
            deployment,
            'Deploy',
            'success',
            (timezone.now() - start).total_seconds()
        )
        broadcast_status(deployment)
        append_log(deployment, f"✓ Deployment successful! ID: {resource.resource_id}\n")

    except Exception as e:
        update_stage(deployment, 'Deploy', 'failed')
        raise e


def _handle_failure(task, deployment, error_msg, reason):
    """Centralized failure handling."""
    logger.error("%s: %s", reason, error_msg)

    if deployment:
        deployment.refresh_from_db()
        if deployment.status != 'CANCELLED':
            deployment.status = 'FAILED'
            deployment.finished_at = timezone.now()
            deployment.build_logs += f"\n✗ {reason}: {error_msg}\n"
            deployment.save()
            broadcast_status(deployment)

            if analyze_failure_task:
                analyze_failure_task.delay(str(deployment.id))

    raise task.retry(exc=Exception(error_msg), countdown=30)


@shared_task(bind=True, max_retries=0)
def one_click_deploy_template_task(self, service_id: str, template_id: str):
    """
    Background orchestration for template deployments.
    """
    # pylint: disable=unused-argument
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        return

    # Load template
    template_path = os.path.join(
        settings.BASE_DIR, 'apps/deployments/fixtures/templates.json'
    )
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            templates = json.load(f)
        template = next((t for t in templates if t.get('id') == template_id), None)
    except Exception: # pylint: disable=broad-exception-caught
        template = None

    # Provision addons
    required_addons = (template.get('required_addons') or []) if template else []

    for addon_type in required_addons:
        addon = Addon.objects.create(
            service=service,
            name=f"{addon_type.lower()}-{service.name}"[:255],
            addon_type=addon_type,
            status=Addon.Status.PROVISIONING,
        )
        try:
            _, url = addon_provisioner.provision(addon)
            addon.connection_url = url
            addon.status = Addon.Status.ACTIVE
            addon.save()

            # Inject Env
            key_map = {'POSTGRES': 'DATABASE_URL', 'REDIS': 'REDIS_URL'}
            key = key_map.get(addon_type, f"{addon_type}_URL")
            EnvironmentVariable.objects.create(
                service=service, key=key, value=url, is_secret=True
            )

        except Exception: # pylint: disable=broad-exception-caught
            addon.status = Addon.Status.FAILED
            addon.save()
            return # Stop deploy

    # Trigger deploy
    provider = service.provider or CloudProvider.objects.filter(is_active=True).first()
    if provider:
        deployment = Deployment.objects.create(
            service=service,
            status='QUEUED',
            commit_hash='template',
            commit_message=f"Template: {template_id}"
        )
        smart_deploy_task.delay(str(deployment.id), str(provider.id))


@shared_task(bind=True)
def provision_addon_task(self, addon_id: str):
    """Legacy addon task."""
    try:
        addon = Addon.objects.get(id=addon_id)
        cid, url = addon_provisioner.provision(addon)
        addon.connection_url = url
        addon.status = Addon.Status.ACTIVE
        addon.coolify_uuid = cid
        addon.save()

        if addon.service:
            key = f"{addon.addon_type}_URL"
            if addon.addon_type == 'POSTGRES':
                key = 'DATABASE_URL'
            elif addon.addon_type == 'REDIS':
                key = 'REDIS_URL'

            EnvironmentVariable.objects.update_or_create(
                service=addon.service,
                key=key,
                defaults={'value': url, 'is_secret': True}
            )
    except Exception as e:
        raise self.retry(exc=e, countdown=30)


@shared_task
def deprovision_addon_task(addon_id: str):
    """Delete addon container."""
    try:
        addon = Addon.objects.get(id=addon_id)
        if addon.coolify_uuid:
            addon_provisioner.deprovision(addon.coolify_uuid, f"addon-{addon.id}")
        addon.status = Addon.Status.DELETED
        addon.save()
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Deprovision failed: %s", e)


@shared_task(bind=True)
def backup_addon_task(self, addon_id: str):
    """Create a backup for the specified addon."""
    try:
        addon = Addon.objects.get(id=addon_id)
        backup = Backup.objects.create(addon=addon, status=Backup.Status.PENDING)
        path = addon_provisioner.create_backup(addon)
        backup.file_path = path
        backup.status = Backup.Status.COMPLETED
        backup.save()
    except Exception as e:
        raise self.retry(exc=e, countdown=30)


@shared_task(bind=True)
def restore_addon_task(self, backup_id: str):
    """Restore a backup to the addon."""
    # pylint: disable=unused-argument
    try:
        backup = Backup.objects.get(id=backup_id)
        addon_provisioner.restore_backup(backup.addon, backup.file_path)
    except Exception as e:
        raise e
