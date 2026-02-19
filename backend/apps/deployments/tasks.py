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
from django.db.models import Sum

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
from .services.backup_service import BackupService
from .services.transfer_service import ServerTransferService
from apps.billing.services.metering import UsageMeter
from apps.billing.models import UsageRecord, UserSubscription, Invoice, PricingPlan, DailyRevenue, InfrastructureCost
from .models_backup import BackupSchedule, ServiceBackup
from .models_transfer import ServerTransfer

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
def smart_deploy_task(self, deployment_id: str, provider_id: str,
                     skip_review: bool = False):
    """
    Orchestrates a deployment using PipelineManager for build steps.

    For fresh GIT deploys (manual): runs analysis only, pauses at REVIEW.
    For rollbacks, restarts, webhooks, and non-GIT: runs full pipeline.

    Args:
        skip_review: If True, bypass the REVIEW gate (used by restarts,
                     webhooks, and any automated deploy path).
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

            # Skip review for: rollbacks, restarts, webhooks
            if deployment.is_rollback or skip_review:
                image_name = manager.run()
            else:
                # Fresh manual deploy → analysis only, pause for review
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

        # ── Real-Time Post-Deploy Health Monitor ──
        # Monitor container logs for ~30s to catch runtime crashes early
        _post_deploy_monitor.delay(
            str(deployment.id),
            str(provider.id),
            resource.resource_id,
            image_name,
        )

    except Exception as e:
        update_stage(deployment, 'Deploy', 'failed')
        raise e


@shared_task(bind=True, max_retries=0, soft_time_limit=120, time_limit=150)
def _post_deploy_monitor(self, deployment_id, provider_id, container_id,
                         image_name):
    """
    Real-time post-deploy health monitor.

    Watches container logs for 30s after deploy. If the container crashes:
    1. Pattern resolver scans logs instantly for known errors (no API call)
    2. If a pattern matches and has an auto-fix → fix + auto-redeploy
    3. If patterns can't explain → escalate to AI models with code context
    """
    import time
    import docker

    try:
        deployment = Deployment.objects.get(id=deployment_id)
        service = deployment.service
    except Deployment.DoesNotExist:
        return

    try:
        client = docker.from_env()
    except Exception:
        logger.warning("Docker not available for post-deploy monitor")
        return

    append_log(deployment, "\n🔍 Post-deploy health monitor active (30s)...\n")
    broadcast_status(deployment)

    # Poll container status for 30 seconds
    crash_detected = False
    container_logs = ""
    for check in range(6):  # 6 checks × 5s = 30s
        time.sleep(5)

        try:
            container = client.containers.get(container_id)
            status = container.status  # running, exited, restarting, dead
            container_logs = container.logs(tail=200).decode(
                'utf-8', errors='replace'
            )

            if status in ('exited', 'dead'):
                crash_detected = True
                append_log(
                    deployment,
                    f"\n🔴 Container crashed (status: {status}) "
                    f"after {(check + 1) * 5}s\n"
                )
                break

            if status == 'restarting':
                # Wait one more cycle to see if it stabilises
                if check >= 2:
                    crash_detected = True
                    append_log(
                        deployment,
                        f"\n🔴 Container stuck in restart loop "
                        f"after {(check + 1) * 5}s\n"
                    )
                    break

        except docker.errors.NotFound:
            crash_detected = True
            append_log(deployment, "\n🔴 Container disappeared after deploy\n")
            break
        except Exception as e:
            logger.warning("Monitor check failed: %s", e)
            continue

    if not crash_detected:
        append_log(deployment, "✅ Container healthy after 30s monitoring.\n")
        broadcast_status(deployment)
        return

    # ── CRASH DETECTED — Run real-time diagnosis ──
    deployment.refresh_from_db()

    # Step 1: Pattern resolver (instant, no API call)
    from apps.deployments.services.error_resolver import diagnose_runtime_logs
    results = diagnose_runtime_logs(
        container_logs,
        service=service,
        deployment=deployment,
        auto_apply=True,
    )

    auto_fixed = [r for r in results if r.get('auto_fixed')]

    if auto_fixed:
        # Auto-fix applied — trigger automatic redeploy
        append_log(
            deployment,
            f"\n🔧 {len(auto_fixed)} issue(s) auto-fixed. "
            f"Triggering automatic redeploy...\n"
        )
        deployment.status = 'FAILED'
        deployment.build_logs += f"\n--- Runtime Crash Logs ---\n{container_logs[-3000:]}\n"
        deployment.save()
        broadcast_status(deployment)

        # Create a new deployment with the fix applied
        new_deployment = Deployment.objects.create(
            service=service,
            status='QUEUED',
            commit_hash=deployment.commit_hash,
            commit_message=f"[auto-fix] {', '.join(r['category'] for r in auto_fixed)}",
            is_rollback=False,
        )
        provider = CloudProvider.objects.get(id=provider_id)
        smart_deploy_task.delay(
            str(new_deployment.id), str(provider.id), skip_review=True
        )
        return

    # Step 2: No pattern match — escalate to AI models
    _escalate_to_ai(deployment, service, container_logs)

    # Mark deployment as failed
    deployment.status = 'FAILED'
    deployment.build_logs += f"\n--- Runtime Crash Logs ---\n{container_logs[-3000:]}\n"
    deployment.finished_at = timezone.now()
    deployment.save()
    broadcast_status(deployment)


def _escalate_to_ai(deployment, service, container_logs):
    """
    Escalate an unknown runtime error to AI models with full code context.
    Uses all configured AI providers via ask_with_fallback.
    """
    try:
        from apps.intelligence.providers import ask_with_fallback

        # Build rich context: logs + service info + env vars (masked)
        env_summary = ", ".join(
            f"{ev.key}={'***' if ev.is_secret else ev.value}"
            for ev in service.env_vars.all()
        )

        prompt = (
            f"A deployed container for service '{service.name}' crashed immediately "
            f"after deployment. Analyze the logs and provide:\n"
            f"1. Root cause of the crash\n"
            f"2. Specific fix (env var to add, config to change, code to fix)\n"
            f"3. Whether this can be auto-fixed by the platform\n\n"
            f"Service: {service.name}\n"
            f"Deploy type: {service.deploy_type}\n"
            f"Image: {service.docker_image or 'built from git'}\n"
            f"Git repo: {service.git_url}\n"
            f"Env vars: {env_summary}\n\n"
            f"--- CONTAINER LOGS (last 200 lines) ---\n"
            f"{container_logs[-4000:]}\n"
            f"--- END LOGS ---\n\n"
            f"Return a JSON object:\n"
            f'{{\n'
            f'  "root_cause": "Brief description",\n'
            f'  "fix": "Specific actionable fix",\n'
            f'  "env_vars_needed": {{"KEY": "value_or_empty"}},\n'
            f'  "auto_fixable": true/false,\n'
            f'  "severity": "critical/warning/info"\n'
            f'}}\n'
        )

        response, provider_name = ask_with_fallback(prompt)
        deployment.ai_diagnosis = response
        deployment.save(update_fields=['ai_diagnosis'])

        append_log(
            deployment,
            f"\n🤖 AI Diagnosis ({provider_name}):\n{response[:2000]}\n"
        )

        # Try to parse and auto-apply AI suggestions
        from apps.deployments.utils import parse_ai_resource_recommendation
        parsed = parse_ai_resource_recommendation(response)
        if parsed and parsed.get('env_vars_needed'):
            from apps.deployments.services.error_resolver import _apply_fix
            fix = {'env': parsed['env_vars_needed']}
            import re as _re
            action = _apply_fix(fix, _re.match('', ''), '', service, deployment)
            if action:
                append_log(deployment, f"  ✅ AI-suggested fix applied: {action}\n")

    except Exception as e:
        logger.warning("AI escalation failed for deployment %s: %s",
                       deployment.id, e)
        append_log(deployment, f"\n🤖 AI diagnosis unavailable: {e}\n")


def _handle_failure(task, deployment, error_msg, reason):
    """Centralized failure handling with pattern resolver + AI escalation."""
    logger.error("%s: %s", reason, error_msg)

    if deployment:
        deployment.refresh_from_db()
        if deployment.status != 'CANCELLED':
            deployment.status = 'FAILED'
            deployment.finished_at = timezone.now()
            deployment.build_logs += f"\n✗ {reason}: {error_msg}\n"
            deployment.save()
            broadcast_status(deployment)

            # Step 1: Pattern resolver on build logs (instant)
            try:
                from apps.deployments.services.error_resolver import (
                    diagnose_runtime_logs,
                )
                diagnose_runtime_logs(
                    deployment.build_logs,
                    service=deployment.service,
                    deployment=deployment,
                    auto_apply=True,
                )
            except Exception as e:
                logger.warning("Pattern resolver failed: %s", e)

            # Step 2: AI diagnosis (async)
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

@shared_task(bind=True, soft_time_limit=3600, time_limit=3900)
def create_service_backup_task(self, service_id, backup_type='MANUAL'):
    service = BackupService()
    service.backup_service(service_id)

@shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
def create_server_backup_task(self):
    service = BackupService()
    service.backup_server()

@shared_task(bind=True, soft_time_limit=3600)
def restore_service_backup_task(self, backup_id, target_service_id=None):
    service = BackupService()
    service.restore_service(backup_id, target_service_id)

@shared_task
def cleanup_old_backups_task():
    """Delete backups older than retention_days per schedule."""
    from datetime import timedelta

    schedules = BackupSchedule.objects.filter(enabled=True)
    for schedule in schedules:
        if schedule.service:
            # Service level
            cutoff = timezone.now() - timedelta(days=schedule.retention_days)
            old_backups = ServiceBackup.objects.filter(
                service=schedule.service,
                created_at__lt=cutoff
            )
            for backup in old_backups:
                # Delete file
                if backup.file_path and os.path.exists(backup.file_path):
                    try:
                        os.remove(backup.file_path)
                    except OSError as e:
                        logger.warning(f"Error deleting backup file {backup.file_path}: {e}")
                backup.delete()

@shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
def execute_server_transfer_task(self, transfer_id):
    transfer = ServerTransfer.objects.get(id=transfer_id)
    ServerTransferService(transfer).execute()

@shared_task(bind=True)
def rollback_transfer_task(self, transfer_id):
    transfer = ServerTransfer.objects.get(id=transfer_id)
    ServerTransferService(transfer).rollback()
