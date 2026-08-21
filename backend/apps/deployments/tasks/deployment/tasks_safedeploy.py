import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from apps.addons.services.addon_provisioner import addon_provisioner

from apps.deployments.constants import (
    TASK_TIME_LIMIT_DATA_SYNC,
    TASK_TIME_LIMIT_MEDIUM,
    TASK_TIME_LIMIT_QUICK,
)
from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    Service,
)
from apps.deployments.models.addons import Addon
from apps.deployments.models.safedeploy import (
    DatabaseClone,
    DeploymentArtifact,
    MigrationValidation,
    PreviewEnvironment,
)
from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
from apps.deployments.services.safedeploy.migration_environment import (
    build_migration_environment,
)
from apps.deployments.services.safedeploy.postgres_snapshot_manager import (
    PostgresSnapshotManager,
)

logger = logging.getLogger(__name__)

# Sensitive env vars that must NOT leak from parent to preview migration
_MIGRATION_SENSITIVE_BLOCKLIST = {
    'SECRET_KEY', 'DJANGO_SECRET_KEY', 'FIELD_ENCRYPTION_KEY',
    'INTERNAL_API_SECRET', 'GATEWAY_SECRET', 'JWT_SECRET',
    'SMSLY_ENCRYPTION_KEY', 'ENCRYPTION_KEY', 'SIGNING_KEY',
    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
    'OPENAI_API_KEY', 'GEMINI_API_KEY', 'CLAUDE_API_KEY',
    'ANTHROPIC_API_KEY', 'GROK_API_KEY',
    'STRIPE_SECRET_KEY', 'PAYPAL_CLIENT_SECRET',
    'TWILIO_AUTH_TOKEN', 'SENDGRID_API_KEY',
    'REDIS_PASSWORD', 'RABBITMQ_PASSWORD',
    'CELERY_BROKER_PASSWORD', 'BROKER_PASSWORD',
}


def _preview_service_name(preview: PreviewEnvironment) -> str:
    return f"preview-{preview.id.hex}"


def _make_clone_database_name(source_db_name: str, branch_name: str, commit_sha: str) -> str:
    raw_branch = ''.join(c if c.isalnum() or c == '_' else '_' for c in branch_name)
    raw_source = ''.join(c if c.isalnum() or c == '_' else '_' for c in source_db_name)
    digest = hashlib.sha256(f"{source_db_name}:{branch_name}:{commit_sha}".encode()).hexdigest()[:16]
    clone_name = f"preview_{raw_source[:18]}_{raw_branch[:20]}_{commit_sha[:8]}_{digest}"
    clone_name = ''.join(c if c.isalnum() or c == '_' else '_' for c in clone_name)
    return clone_name[:63].rstrip('_') or f"preview_{digest}"


def _copy_environment_variables(source: Service, target: Service) -> None:
    for env in source.env_vars.all():
        if env.source == 'ADDON':
            continue
        EnvironmentVariable.objects.update_or_create(
            service=target,
            key=env.key,
            defaults={
                'value': env.value,
                'is_secret': env.is_secret,
                'is_locked': env.is_locked,
                'source': env.source,
            },
        )


def _clone_addon_data(source_addon: Addon, target_addon: Addon) -> None:
    """No-op. Data cloning between parent and preview is disabled to prevent
    data leaks and reduce blast radius. Each preview gets fresh, empty addons."""
    logger.info(
        "Data clone skipped (disabled by design) for %s addon %s -> %s",
        source_addon.addon_type, source_addon.id, target_addon.id,
    )


def _upsert_preview_environment_variables(preview: PreviewEnvironment, target: Service) -> None:
    from apps.deployments.services.safedeploy.branch_preview_manager import (
        BranchPreviewManager,
    )

    preview_vars = BranchPreviewManager().inject_preview_environment_variables(preview)
    secret_keys = {'DATABASE_URL', 'POSTGRES_URL', 'REDIS_URL'}
    for key, value in preview_vars.items():
        EnvironmentVariable.objects.update_or_create(
            service=target,
            key=key,
            defaults={
                'value': value,
                'is_secret': key in secret_keys or key.endswith('_PASSWORD') or key.endswith('_URL'),
                'is_locked': False,
                'source': 'SYSTEM',
            },
        )


def _inject_addon_credentials(addon: Addon) -> None:
    creds = addon.parsed_credentials
    for key, value in creds.items():
        EnvironmentVariable.objects.update_or_create(
            service=addon.service,
            key=key,
            defaults={
                'value': value,
                'is_secret': key.endswith('_PASSWORD') or key.endswith('_URL') or key in {'DATABASE_URL', 'REDIS_URL'},
                'source': 'ADDON',
            },
        )

    if addon.addon_type == Addon.Type.POSTGRES and addon.connection_url:
        EnvironmentVariable.objects.update_or_create(
            service=addon.service,
            key='DATABASE_URL',
            defaults={'value': addon.connection_url, 'is_secret': True, 'source': 'ADDON'},
        )
    elif addon.addon_type == Addon.Type.REDIS and addon.connection_url:
        EnvironmentVariable.objects.update_or_create(
            service=addon.service,
            key='REDIS_URL',
            defaults={'value': addon.connection_url, 'is_secret': True, 'source': 'ADDON'},
        )


def _sync_preview_addons(preview: PreviewEnvironment, transient_service: Service) -> None:

    try:
        pass
    except DatabaseClone.DoesNotExist:
        pass

    for addon in preview.service.addons.exclude(status=Addon.Status.DELETED):
        preview_addon_name = f"{addon.name}-preview-{preview.id.hex[:6]}"
        new_addon, _ = Addon.objects.get_or_create(
            service=transient_service,
            name=preview_addon_name,
            addon_type=addon.addon_type,
            defaults={
                'project': transient_service.project,
                'status': Addon.Status.PROVISIONING,
            },
        )
        update_fields = []
        if new_addon.project_id != transient_service.project_id:
            new_addon.project = transient_service.project
            update_fields.append('project')
        if new_addon.name != preview_addon_name:
            new_addon.name = preview_addon_name
            update_fields.append('name')

        if addon.addon_type == Addon.Type.POSTGRES:
            # Provision a fresh, isolated PostgreSQL container for the preview.
            # Never reuse the parent's DB server — the preview gets its own
            # container with its own hostname, credentials, and empty database.
            if update_fields:
                new_addon.save(update_fields=list(set(update_fields)))

            cid, url = addon_provisioner.provision(new_addon)
            new_addon.connection_url = url
            new_addon.coolify_uuid = cid
            new_addon.status = Addon.Status.ACTIVE
            new_addon.save(update_fields=['connection_url', 'coolify_uuid', 'status', 'updated_at'])
            _inject_addon_credentials(new_addon)
            continue

        # Always provision fresh for preview environments — never reuse stale credentials

        if update_fields:
            new_addon.save(update_fields=list(set(update_fields)))

        cid, url = addon_provisioner.provision(new_addon)
        new_addon.connection_url = url
        new_addon.coolify_uuid = cid
        new_addon.status = Addon.Status.ACTIVE
        new_addon.save(update_fields=['connection_url', 'coolify_uuid', 'status', 'updated_at'])

        _inject_addon_credentials(new_addon)


def _dispatch_preview_deployment(deployment: Deployment, provider_id: str | None):
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False) and os.environ.get('SAFEDEPLOY_RUN_EAGER_DEPLOY') != '1':
        logger.info("Skipping preview deployment dispatch while CELERY_TASK_ALWAYS_EAGER is enabled")
        return None

    from apps.deployments.tasks.deployment.tasks_deploy import enqueue_smart_deploy_task
    return enqueue_smart_deploy_task(str(deployment.id), provider_id or "", skip_review=True)  # type: ignore[arg-type]

def checkout_code(repo_url: str, branch: str, commit_sha: str, target_dir: str, token: str | None = None) -> str:
    from apps.cloud.services.git_manager import GitManager
    try:
        result = GitManager.clone_repo(
            repo_url=repo_url,
            branch=branch,
            destination=target_dir,
            token=token,
            commit_hash=commit_sha
        )
        return result if result else ""
    except Exception as e:
        logger.error(f"Git clone failed: {e!s}")
        return ""

@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.deployment.tasks_safedeploy.create_preview_environment_job")
def create_preview_environment_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        preview.status = PreviewEnvironment.Status.BUILDING
        preview.save()
        create_database_clone_job.delay(preview_id)
    except Exception as e:
        logger.error(f"Error in create_preview_environment_job: {e}", exc_info=True)
        try:
            p = PreviewEnvironment.objects.get(id=preview_id)
            p.status = PreviewEnvironment.Status.BUILD_FAILED
            p.save()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to mark preview as BUILD_FAILED: %s", exc)

@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.deployment.tasks_safedeploy.create_database_clone_job")
def create_database_clone_job(preview_id: str):
    """Provision an isolated preview PostgreSQL container (no data clone from production).

    Instead of cloning production data, this provisions a fresh, empty
    PostgreSQL container exclusively for the preview environment. Zero
    data movement from production — no pg_dump, no CREATE TEMPLATE.
    """
    try:
        logger.info("CLONE_TASK >>> START preview_id=%s", preview_id)
        preview = PreviewEnvironment.objects.get(id=preview_id)
        logger.info("CLONE_TASK >>> preview found: status=%s service=%s", preview.status, preview.service.id)

        from django.core.cache import cache
        lock_key = f"preview_clone_lock:{preview.service_id}"
        if not cache.add(lock_key, str(preview.id), timeout=600):
            logger.warning("Another clone in progress for service %s, retrying in 30s", preview.service_id)
            create_database_clone_job.apply_async(args=[preview_id], countdown=30)
            return

        try:
            pg_addon = Addon.objects.filter(
                service=preview.service,
                addon_type=Addon.Type.POSTGRES,
                status=Addon.Status.ACTIVE,
            ).first()

            if not pg_addon:
                logger.info("CLONE_TASK >>> No PostgreSQL addon for service %s, skipping", preview.service.id)
                validation, _ = MigrationValidation.objects.get_or_create(preview_environment=preview)
                validation.status = MigrationValidation.Status.NOT_CONFIGURED
                validation.summary = "No PostgreSQL addon configured; migration validation skipped."
                validation.save()
                preview.status = PreviewEnvironment.Status.TESTS_RUNNING
                preview.save()
                run_preview_tests_job.delay(preview_id)
                return

            # Create a stub DatabaseClone record (no actual DB on parent server).
            # The real isolated container is provisioned by _sync_preview_addons
            # inside provision_preview_service_job.
            clone, _ = DatabaseClone.objects.get_or_create(
                preview_environment=preview,
                defaults={
                    'service': preview.service,
                    'source_environment': 'production',
                    'source_database_name': '(isolated)',
                    'clone_database_name': '(isolated)',
                    'status': DatabaseClone.Status.READY,
                },
            )

            # Provision the preview container + its own fresh PG addon first,
            # then run migration validation against that isolated database.
            preview.status = PreviewEnvironment.Status.PROVISIONING
            preview.save()
            provision_preview_service_job.delay(preview_id)

        finally:
            cache.delete(lock_key)
    except Exception as e:
        logger.error("CLONE_TASK >>> EXCEPTION: %s", e, exc_info=True)
        try:
            p = PreviewEnvironment.objects.get(id=preview_id)
            p.status = PreviewEnvironment.Status.DB_CLONE_FAILED
            p.error_message = str(e)
            p.save()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to mark preview as DB_CLONE_FAILED: %s", exc)

@shared_task(soft_time_limit=TASK_TIME_LIMIT_DATA_SYNC[0], time_limit=TASK_TIME_LIMIT_DATA_SYNC[1], name="apps.deployments.tasks.deployment.tasks_safedeploy.run_migration_validation_job")
def run_migration_validation_job(preview_id: str):
    workspace_dir = None
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)

        validation, _ = MigrationValidation.objects.get_or_create(preview_environment=preview)

        repo_url = preview.service.repository_url
        if not repo_url:
            logger.error(f"Service {preview.service.id} has no repository URL configured")
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = "No repository URL configured"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        workspace_dir = tempfile.mkdtemp(prefix=f"preview_{preview.id}_")
        from apps.deployments.utils import get_github_oauth_token_for_user
        token = get_github_oauth_token_for_user(preview.service.owner)
        cloned_path = checkout_code(repo_url, preview.branch_name, preview.commit_sha, workspace_dir, token)
        if not cloned_path:
            raise Exception("Failed to clone repository")

        # Use the preview addon's own isolated database URL (never the parent's).
        from apps.deployments.models.addons import Addon
        preview_pg = Addon.objects.filter(
            service__name__startswith=f"preview-{preview.id.hex}",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
        ).first()

        if not preview_pg or not preview_pg.connection_url:
            has_pg_addon = Addon.objects.filter(
                service=preview.service, addon_type=Addon.Type.POSTGRES
            ).exists()
            if has_pg_addon:
                validation.status = MigrationValidation.Status.FAILED
                validation.error_message = "No ready preview database available"
                validation.save()
                preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
                preview.save()
                return
            else:
                validation.status = MigrationValidation.Status.NOT_CONFIGURED
                validation.save()
                preview.status = PreviewEnvironment.Status.TESTS_RUNNING
                preview.save()
                run_preview_tests_job.delay(preview_id)
                return

        db_url = preview_pg.connection_url

        # Safety: reject if the preview URL somehow matches production
        prod_db_url = None
        for env_var in preview.service.env_vars.all():
            if env_var.key == 'DATABASE_URL':
                prod_db_url = env_var.value

        if prod_db_url and db_url == prod_db_url:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = "SAFETY ABORT: Preview DB URL matched Production DB URL."
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        # Collect non-sensitive service env vars for the migration environment
        service_env_vars = {
            env_var.key: env_var.value
            for env_var in preview.service.env_vars.all()
            if env_var.key.upper().replace('-', '_') not in _MIGRATION_SENSITIVE_BLOCKLIST
        }

        # Build isolated venv with dependencies installed and settings discovered
        mig_env = build_migration_environment(cloned_path, db_url, service_env_vars)
        if not mig_env.ok:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = f"Migration environment setup failed: {mig_env.error}"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        adapter = DjangoAdapter(python_bin=mig_env.python_bin)

        if not adapter.detect(cloned_path):
            validation.status = MigrationValidation.Status.NOT_CONFIGURED
            validation.save()
            preview.status = PreviewEnvironment.Status.TESTS_RUNNING
            preview.save()
            run_preview_tests_job.delay(preview_id)
            return

        env = mig_env.env

        # Run Check
        rc, out, err = adapter.run_check(cloned_path, env)
        if rc != 0:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = f"Django check failed: {err[-500:] if err else 'unknown'}"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        # Makemigrations --check
        rc, mm_out, mm_err = adapter.run_makemigrations_check(cloned_path, env)
        if rc != 0:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = f"makemigrations --check failed: {mm_err[-500:] if mm_err else 'unknown'}"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        # Showmigrations
        rc, plan_out, err = adapter.run_showmigrations(cloned_path, env)
        DeploymentArtifact.objects.create(service=preview.service, preview_environment=preview, artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_PLAN, content=plan_out)

        # Risk Classification
        operations = adapter.inspect_migration_files(cloned_path)
        risk_report = adapter.classify_migration_risk(operations)

        validation.risk_level = risk_report['risk_level']
        validation.risk_score = risk_report['risk_score']
        validation.summary = risk_report['summary']
        validation.reasons = risk_report['reasons']
        validation.auto_deploy_policy = risk_report['auto_deploy_policy']
        validation.requires_backup = risk_report['requires_backup']

        # Migrate against the cloned database
        rc, out, err = adapter.run_migrate(cloned_path, env)
        DeploymentArtifact.objects.create(service=preview.service, preview_environment=preview, artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_OUTPUT, content=f"RC: {rc}\n{out}\n{err}")

        if rc != 0:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = f"Migration apply failed: {err[-500:] if err else 'unknown'}"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        # TODO: Detect data-vs-schema mismatch.
        # After the empty-clone migration succeeds, sample production data into
        # the clone (e.g. pg_dump --data-only --rows-per-insert=1000 per non-empty
        # table) and re-run makemigrations --check. If the data violates the new
        # schema, mark validation as FAILED with a clear message such as
        # "Migration may fail on production data — {n} tables have data that
        # conflicts with new schema." Stretch goal; implementation deferred.

        validation.status = MigrationValidation.Status.PASSED
        validation.save()

        DeploymentArtifact.objects.create(service=preview.service, preview_environment=preview, artifact_type=DeploymentArtifact.ArtifactType.RISK_REPORT, content=validation.summary)

        preview.status = PreviewEnvironment.Status.TESTS_RUNNING
        preview.save()
        run_preview_tests_job.delay(preview_id)
    except Exception as e:
        if 'preview' in locals():
            try:
                preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
                preview.error_message = str(e)
                preview.save()
            except Exception as exc:
                logger.debug("Failed to update preview environment status: %s", exc)
    finally:
        if workspace_dir:
            shutil.rmtree(workspace_dir, ignore_errors=True)

@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.deployment.tasks_safedeploy.run_preview_tests_job")
def run_preview_tests_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        # Not fully implemented for v1, mark explicitly skipped
        DeploymentArtifact.objects.create(
            service=preview.service,
            preview_environment=preview,
            artifact_type=DeploymentArtifact.ArtifactType.TEST_OUTPUT,
            content="Tests: skipped by configuration (not implemented for v1)"
        )

        # Migration + tests passed — now trigger the actual deployment.
        transient_service_name = _preview_service_name(preview)
        transient_service = Service.objects.filter(name=transient_service_name, is_preview=True).first()
        if not transient_service:
            logger.error("No transient service found for preview %s, cannot deploy", preview_id)
            return

        deployment = Deployment.objects.create(
            service=transient_service,
            commit_hash=preview.commit_sha,
            branch=preview.branch_name,
            commit_message=f"SafeDeploy preview for {preview.branch_name}"
        )
        parent = preview.service
        provider_id = str(parent.provider.id) if parent.provider else None
        _dispatch_preview_deployment(deployment, provider_id)

        preview.status = PreviewEnvironment.Status.HEALTH_CHECK_RUNNING
        preview.save()
    except Exception as e:
        logger.error(f"Error in run_preview_tests_job for {preview_id}: {e}", exc_info=True)
        try:
            preview = PreviewEnvironment.objects.get(id=preview_id)
            preview.status = PreviewEnvironment.Status.TESTS_FAILED
            preview.save(update_fields=['status', 'updated_at'])
        except Exception as inner_exc:
            logger.warning("Failed to mark preview %s as TESTS_FAILED: %s", preview_id, inner_exc)

@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.deployment.tasks_safedeploy.provision_preview_service_job")
def provision_preview_service_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        parent = preview.service

        # 1. Create or update transient service
        transient_service_name = _preview_service_name(preview)
        transient_service, created = Service.objects.get_or_create(
            name=transient_service_name,
            defaults={
                'owner': parent.owner,
                'project': parent.project,
                'repository_url': parent.repository_url,
                'branch': preview.branch_name,
                'parent_service': parent,
                'is_preview': True,
                'public_domain': preview.preview_url.replace("https://", "").replace("http://", "") if preview.preview_url else "",
                'custom_domains': [],
                'provider': parent.provider,
                'server': parent.server,
                'deploy_type': parent.deploy_type,
                'buildpack': parent.buildpack,
                'docker_image': parent.docker_image,
                'build_command': parent.build_command,
                'start_command': parent.start_command,
                'root_directory': parent.root_directory,
                'internal_port': parent.internal_port,
                'cpu_cores': parent.cpu_cores,
                'memory_mb': parent.memory_mb,
                'health_check_path': parent.health_check_path,
                'health_check_port': parent.health_check_port,
                'deploy_mode': parent.deploy_mode,
                'compose_file': parent.compose_file,
                'compose_main_service': parent.compose_main_service,
                'env_scan_depth': 'shallow',
            }
        )

        if not created:
            transient_service.owner = parent.owner
            transient_service.project = parent.project
            transient_service.repository_url = parent.repository_url
            transient_service.branch = preview.branch_name
            transient_service.parent_service = parent
            transient_service.is_preview = True
            transient_service.public_domain = preview.preview_url.replace("https://", "").replace("http://", "") if preview.preview_url else transient_service.public_domain
            transient_service.custom_domains = []
            transient_service.provider = parent.provider
            transient_service.server = parent.server
            transient_service.deploy_type = parent.deploy_type
            transient_service.buildpack = parent.buildpack
            transient_service.docker_image = parent.docker_image
            transient_service.build_command = parent.build_command
            transient_service.start_command = parent.start_command
            transient_service.root_directory = parent.root_directory
            transient_service.internal_port = parent.internal_port
            transient_service.cpu_cores = parent.cpu_cores
            transient_service.memory_mb = parent.memory_mb
            transient_service.health_check_path = parent.health_check_path
            transient_service.health_check_port = parent.health_check_port
            transient_service.deploy_mode = parent.deploy_mode
            transient_service.compose_file = parent.compose_file
            transient_service.compose_main_service = parent.compose_main_service
            transient_service.save()

        # 2. Sync environment variables and isolated preview overrides (Enterprise Option A: clean start, no parent env copy).
        _upsert_preview_environment_variables(preview, transient_service)

        # 3. Provision addons — each gets its own fresh, isolated container (no parent data).
        _sync_preview_addons(preview, transient_service)
        _upsert_preview_environment_variables(preview, transient_service)

        # 3b. AI Senate: fill any remaining placeholder/mock env vars on the preview service.
        #     Ensures the preview gets real production values, not parent heuristics.
        if getattr(settings, "SENATE_ENABLED", True):
            try:
                from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService
                _sugg, _inj = EnvironmentIntelligenceService.apply_intelligence_to_service(
                    transient_service, scan_results={}
                )
                if _inj:
                    logger.info(
                        "SafeDeploy preview %s: AI Senate auto-filled %d env vars: %s",
                        preview_id, len(_inj), ", ".join(_inj),
                    )
            except Exception as _senate_err:
                logger.warning("SafeDeploy preview %s: AI Senate enrichment failed: %s", preview_id, _senate_err)

        # 4. Run migration validation against the isolated preview database
        #    (now that the fresh PG container exists). The deployment is triggered
        #    only after migration validation and tests pass.
        preview.status = PreviewEnvironment.Status.MIGRATION_RUNNING
        preview.save()
        run_migration_validation_job.delay(preview_id)
    except Exception as e:
        logger.error(f"Failed to provision preview environment {preview_id}: {e}", exc_info=True)
        try:
            p = PreviewEnvironment.objects.get(id=preview_id)
            p.status = PreviewEnvironment.Status.HEALTH_CHECK_FAILED
            p.error_message = str(e)
            p.save()
        except Exception as exc:
            logger.debug("Failed to update preview status on provision failure: %s", exc)

@shared_task(soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1], name="apps.deployments.tasks.deployment.tasks_safedeploy.run_preview_health_check_job")
def run_preview_health_check_job(preview_id: str):
    try:
        from apps.deployments.services.safedeploy.health_checks import (
            perform_health_check,
        )
        preview = PreviewEnvironment.objects.get(id=preview_id)
        if not preview.preview_url:
            preview.status = PreviewEnvironment.Status.HEALTH_CHECK_FAILED
            preview.error_message = "No preview URL configured"
            preview.save()
            return
        ok, result = perform_health_check(preview.preview_url, service=preview.service)
        if ok:
            preview.status = PreviewEnvironment.Status.READY
        else:
            preview.status = PreviewEnvironment.Status.HEALTH_CHECK_FAILED
            preview.error_message = result.error_message or "Health check returned non-2xx"
        preview.save()
    except Exception as e:
        logger.error(f"Health check failed for preview {preview_id}: {e}", exc_info=True)
        try:
            preview = PreviewEnvironment.objects.get(id=preview_id)
            preview.status = PreviewEnvironment.Status.HEALTH_CHECK_FAILED
            preview.save(update_fields=['status', 'updated_at'])
        except Exception as inner_exc:
            logger.warning("Failed to mark preview %s as HEALTH_CHECK_FAILED: %s", preview_id, inner_exc)

@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.deployment.tasks_safedeploy.destroy_preview_environment_job")
def destroy_preview_environment_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)

        # 1. Destroy Transient Service
        transient_service_name = _preview_service_name(preview)
        transient_service = Service.objects.filter(name=transient_service_name, is_preview=True).first()
        if transient_service:
            # Deprovision the preview's own PostgreSQL addon container first
            from apps.deployments.models.addons import Addon
            preview_pg_addons = Addon.objects.filter(
                service=transient_service,
                addon_type=Addon.Type.POSTGRES,
            )
            for pg_addon in preview_pg_addons:
                try:
                    if pg_addon.coolify_uuid:
                        addon_provisioner.deprovision_dispatch(pg_addon.coolify_uuid, pg_addon)
                    else:
                        container_name = f"smsly-addon-{pg_addon.addon_type.lower()}-{pg_addon.id}"
                        subprocess.run(['docker', 'rm', '-f', container_name], capture_output=True, check=False)
                except Exception as deprovision_exc:
                    logger.warning("Failed to deprovision preview PG addon %s: %s", pg_addon.id, deprovision_exc)

            transient_service.status = Service.Status.DELETION_PENDING
            transient_service.save()
            from apps.deployments.tasks import delete_service_task
            delete_service_task.delay(str(transient_service.id))
            time.sleep(5)

        # 2. Destroy Database Clone (temp migration-validation DB on parent server)
        try:
            db_clone = preview.database_clone
        except DatabaseClone.DoesNotExist:
            db_clone = None

        if db_clone:
            db_manager = PostgresSnapshotManager(admin_db_url=db_clone.clone_database_url_secret_ref)
            db_manager.destroy_clone(db_clone.clone_database_name)
            db_clone.status = DatabaseClone.Status.DESTROYED
            db_clone.save()

        # 3. Destroy PreviewEnvironment record
        preview.status = PreviewEnvironment.Status.DESTROYED
        preview.save()
        preview.delete()

    except PreviewEnvironment.DoesNotExist:
        logger.warning("Preview %s already deleted", preview_id)
    except Exception as e:
        logger.error(f"Failed to destroy preview environment {preview_id}: {e}", exc_info=True)
        try:
            p = PreviewEnvironment.objects.get(id=preview_id)
            p.status = PreviewEnvironment.Status.DESTROY_FAILED
            p.error_message = str(e)
            p.save()
        except Exception as exc:
            logger.debug("Failed to update preview status on destroy failure: %s", exc)


@shared_task(soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1], name="apps.deployments.tasks.deployment.tasks_safedeploy.expire_stale_previews_job")
def expire_stale_previews_job():
    from apps.deployments.models.safedeploy import PreviewEnvironment
    now = timezone.now()
    expired = PreviewEnvironment.objects.filter(
        expires_at__lt=now,
        status__in=[
            PreviewEnvironment.Status.READY,
            PreviewEnvironment.Status.HEALTH_CHECK_FAILED,
            PreviewEnvironment.Status.TESTS_FAILED,
            PreviewEnvironment.Status.MIGRATION_FAILED,
            PreviewEnvironment.Status.DB_CLONE_FAILED,
            PreviewEnvironment.Status.DESTROY_FAILED,
        ],
    )
    for preview in expired:
        try:
            preview.status = PreviewEnvironment.Status.DESTROYING
            preview.save()
            destroy_preview_environment_job.delay(str(preview.id))
            logger.info("Expired preview %s scheduled for destruction", preview.id)
        except Exception as exc:
            logger.error("Failed to expire preview %s: %s", preview.id, exc)
