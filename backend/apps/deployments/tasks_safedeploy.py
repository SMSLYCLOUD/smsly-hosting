import logging
import os
import shutil
import tempfile
import time
import subprocess
import hashlib
from celery import shared_task
from django.conf import settings
from apps.deployments.models_safedeploy import PreviewEnvironment, DatabaseClone, MigrationValidation, DeploymentArtifact
from apps.deployments.services.safedeploy.postgres_snapshot_manager import PostgresSnapshotManager
from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
from apps.deployments.models_core import Service, EnvironmentVariable, Deployment
from apps.deployments.models_addons import Addon
logger = logging.getLogger(__name__)


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


def _upsert_preview_environment_variables(preview: PreviewEnvironment, target: Service) -> None:
    from apps.deployments.services.safedeploy.branch_preview_manager import BranchPreviewManager

    preview_vars = BranchPreviewManager().inject_preview_environment_variables(preview)
    secret_keys = {'DATABASE_URL', 'POSTGRES_URL', 'REDIS_URL'}
    for key, value in preview_vars.items():
        EnvironmentVariable.objects.update_or_create(
            service=target,
            key=key,
            defaults={
                'value': value,
                'is_secret': key in secret_keys or key.endswith('_PASSWORD') or key.endswith('_URL'),
                'is_locked': True,
                'source': 'SYSTEM',
            },
        )


def _inject_addon_credentials(addon: Addon) -> None:
    creds = addon.parsed_credentials
    for key, value in creds.items():
        EnvironmentVariable.objects.get_or_create(
            service=addon.service,
            key=key,
            defaults={
                'value': value,
                'is_secret': key.endswith('_PASSWORD') or key.endswith('_URL') or key in {'DATABASE_URL', 'REDIS_URL'},
                'source': 'ADDON',
            },
        )

    if addon.addon_type == Addon.Type.POSTGRES and addon.connection_url:
        EnvironmentVariable.objects.get_or_create(
            service=addon.service,
            key='DATABASE_URL',
            defaults={'value': addon.connection_url, 'is_secret': True, 'source': 'ADDON'},
        )
    elif addon.addon_type == Addon.Type.REDIS and addon.connection_url:
        EnvironmentVariable.objects.get_or_create(
            service=addon.service,
            key='REDIS_URL',
            defaults={'value': addon.connection_url, 'is_secret': True, 'source': 'ADDON'},
        )


def _sync_preview_addons(preview: PreviewEnvironment, transient_service: Service) -> None:
    from services.addon_provisioner import addon_provisioner

    try:
        db_clone = preview.database_clone
    except DatabaseClone.DoesNotExist:
        db_clone = None

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

        if addon.addon_type == Addon.Type.POSTGRES and db_clone:
            if db_clone.status != DatabaseClone.Status.READY:
                raise RuntimeError("PostgreSQL clone is not ready")
            new_addon.connection_url = db_clone.clone_database_url_secret_ref
            new_addon.status = Addon.Status.ACTIVE
            update_fields.extend(['connection_url', 'status'])
            new_addon.save(update_fields=list(set(update_fields)) or None)
            _inject_addon_credentials(new_addon)
            continue

        if new_addon.status == Addon.Status.ACTIVE and new_addon.connection_url:
            if update_fields:
                new_addon.save(update_fields=list(set(update_fields)))
            _inject_addon_credentials(new_addon)
            continue

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

    from apps.deployments.tasks import enqueue_smart_deploy_task
    return enqueue_smart_deploy_task(str(deployment.id), provider_id, skip_review=True)

def checkout_code(repo_url: str, branch: str, commit_sha: str, target_dir: str, token: str = None) -> str:
    from apps.deployments.services.git_manager import GitManager
    try:
        return GitManager.clone_repo(
            repo_url=repo_url,
            branch=branch,
            destination=target_dir,
            token=token,
            commit_hash=commit_sha
        )
    except Exception as e:
        logger.error(f"Git clone failed: {str(e)}")
        return None

@shared_task
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

@shared_task
def create_database_clone_job(preview_id: str):
    try:
        logger.error(f"CLONE_TASK >>> START preview_id={preview_id}")
        preview = PreviewEnvironment.objects.get(id=preview_id)
        logger.error(f"CLONE_TASK >>> preview found: status={preview.status} service={preview.service.id}")

        from django.core.cache import cache
        lock_key = f"preview_clone_lock:{preview.service_id}"
        if not cache.add(lock_key, str(preview.id), timeout=600):
            logger.warning("Another clone in progress for service %s, retrying in 30s", preview.service_id)
            # Soft guard: re-queue with a delay because the task is not bound
            # to self (would need bind=True for self.retry()).
            create_database_clone_job.apply_async(args=[preview_id], countdown=30)
            return

        try:
            # Find the service's PostgreSQL addon to determine the source database name
            pg_addon = Addon.objects.filter(
                service=preview.service,
                addon_type=Addon.Type.POSTGRES,
                status=Addon.Status.ACTIVE,
            ).first()

            if not pg_addon:
                logger.error("CLONE_TASK >>> No PostgreSQL addon for service %s, skipping DB clone", preview.service.id)
                validation, _ = MigrationValidation.objects.get_or_create(preview_environment=preview)
                validation.status = MigrationValidation.Status.NOT_CONFIGURED
                validation.summary = "No PostgreSQL addon configured; migration validation skipped."
                validation.save()
                preview.status = PreviewEnvironment.Status.TESTS_RUNNING
                preview.save()
                run_preview_tests_job.delay(preview_id)
                return

            logger.error(f"CLONE_TASK >>> addon found: {pg_addon.id} url={pg_addon.connection_url[:60]}")

            # Extract the actual database name from the addon's connection URL
            from urllib.parse import urlparse
            parsed = urlparse(pg_addon.connection_url)
            source_db_name = parsed.path.lstrip('/') if parsed.path else None
            if not source_db_name:
                logger.error("CLONE_TASK >>> Could not determine database name from addon %s URL for service %s",
                             pg_addon.id, preview.service.id)
                preview.status = PreviewEnvironment.Status.DB_CLONE_FAILED
                preview.error_message = "Could not determine source database name"
                preview.save()
                return

            clone_db_name = _make_clone_database_name(source_db_name, preview.branch_name, preview.commit_sha)
            logger.error(f"CLONE_TASK >>> source_db={source_db_name} clone_db={clone_db_name}")

            clone, created = DatabaseClone.objects.get_or_create(
                preview_environment=preview,
                defaults={
                    'service': preview.service,
                    'source_environment': 'production',
                    'source_database_name': source_db_name,
                    'clone_database_name': clone_db_name,
                    'status': DatabaseClone.Status.CREATING,
                },
            )
            if not created:
                clone.source_database_name = source_db_name
                clone.clone_database_name = clone_db_name
                clone.status = DatabaseClone.Status.CREATING
                clone.error_message = ""
                clone.save()

            preview.status = PreviewEnvironment.Status.DB_CLONE_CREATING
            preview.save()
            logger.error(f"CLONE_TASK >>> calling create_clone...")

            db_manager = PostgresSnapshotManager(admin_db_url=pg_addon.connection_url)
            success = db_manager.create_clone(clone.source_database_name, clone.clone_database_name, allow_production_disruption=False)
            logger.error(f"CLONE_TASK >>> create_clone returned: {success}")

            if success:
                clone.status = DatabaseClone.Status.READY
                clone.clone_database_url_secret_ref = db_manager.get_clone_url(clone.clone_database_name)
                clone.save()

                preview.status = PreviewEnvironment.Status.MIGRATION_RUNNING
                preview.save()
                run_migration_validation_job.delay(preview_id)
            else:
                clone.status = DatabaseClone.Status.FAILED
                clone.error_message = clone.error_message or "PostgresSnapshotManager.create_clone returned False"
                clone.save()
                preview.status = PreviewEnvironment.Status.DB_CLONE_FAILED
                preview.error_message = clone.error_message
                preview.save()
                logger.error(f"CLONE_TASK >>> FAILED: {clone.error_message}")
        finally:
            cache.delete(lock_key)
    except Exception as e:
        logger.error(f"CLONE_TASK >>> EXCEPTION: {e}", exc_info=True)
        logger.error(f"Error in create_database_clone_job: {e}", exc_info=True)
        try:
            p = PreviewEnvironment.objects.get(id=preview_id)
            p.status = PreviewEnvironment.Status.DB_CLONE_FAILED
            p.error_message = str(e)
            p.save()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to mark preview as DB_CLONE_FAILED: %s", exc)

@shared_task
def run_migration_validation_job(preview_id: str):
    workspace_dir = None
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        adapter = DjangoAdapter()

        workspace_dir = tempfile.mkdtemp(prefix=f"preview_{preview.id}_")
        repo_url = preview.service.repository_url
        cloned_path = workspace_dir
        if repo_url:
             from apps.deployments.utils import get_github_oauth_token_for_user
             token = get_github_oauth_token_for_user(preview.service.owner)
             cloned_path = checkout_code(repo_url, preview.branch_name, preview.commit_sha, workspace_dir, token)
             if not cloned_path: raise Exception("Failed to clone repository")

        if not repo_url:
            logger.error(f"Service {preview.service.id} has no repository URL configured")
            validation, _ = MigrationValidation.objects.get_or_create(preview_environment=preview)
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = "No repository URL configured"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        validation, _ = MigrationValidation.objects.get_or_create(preview_environment=preview)

        try:
            db_clone = preview.database_clone
        except DatabaseClone.DoesNotExist:
            db_clone = None

        if not db_clone or db_clone.status != DatabaseClone.Status.READY:
            # Check if this service has a PostgreSQL addon at all
            from apps.deployments.models_addons import Addon
            has_pg_addon = Addon.objects.filter(
                service=preview.service, addon_type=Addon.Type.POSTGRES
            ).exists()
            if has_pg_addon:
                validation.status = MigrationValidation.Status.FAILED
                validation.error_message = "No ready database clone available"
                validation.save()
                preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
                preview.save()
                return
            else:
                # No DB means no migrations to validate — skip gracefully
                validation.status = MigrationValidation.Status.NOT_CONFIGURED
                validation.save()
                preview.status = PreviewEnvironment.Status.TESTS_RUNNING
                preview.save()
                run_preview_tests_job.delay(preview_id)
                return

        clone_url = db_clone.clone_database_url_secret_ref

        # Check production DB safety
        prod_db_url = None
        for env_var in preview.service.env_vars.all():
            if env_var.key == 'DATABASE_URL':
                prod_db_url = env_var.value

        if prod_db_url and clone_url == prod_db_url:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = "SAFETY ABORT: Preview DB URL matched Production DB URL."
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        if not adapter.detect(cloned_path):
            validation.status = MigrationValidation.Status.NOT_CONFIGURED
            validation.save()
            preview.status = PreviewEnvironment.Status.TESTS_RUNNING
            preview.save()
            run_preview_tests_job.delay(preview_id)
            return

        env = {"DATABASE_URL": clone_url}

        # Run Check
        rc, out, err = adapter.run_check(cloned_path, env)
        if rc != 0:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = "Django check failed"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        # Makemigrations --check
        rc, mm_out, mm_err = adapter.run_makemigrations_check(cloned_path, env)
        if rc != 0:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = f"makemigrations --check failed: {mm_err}"
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

        # Migrate
        rc, out, err = adapter.run_migrate(cloned_path, env)
        DeploymentArtifact.objects.create(service=preview.service, preview_environment=preview, artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_OUTPUT, content=f"RC: {rc}\n{out}\n{err}")

        if rc != 0:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = "Migration apply failed."
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
            except Exception:
                pass
    finally:
        if workspace_dir:
            shutil.rmtree(workspace_dir, ignore_errors=True)

@shared_task
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
        preview.status = PreviewEnvironment.Status.HEALTH_CHECK_RUNNING
        preview.save()
        provision_preview_service_job.delay(preview_id)
    except Exception as e:
        logger.error(f"Error in run_preview_tests_job for {preview_id}: {e}", exc_info=True)

@shared_task
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

        # 2. Sync environment variables and isolated preview overrides.
        _copy_environment_variables(parent, transient_service)
        _upsert_preview_environment_variables(preview, transient_service)

        # 3. Duplicate/provision addons before deployment so injected URLs exist.
        _sync_preview_addons(preview, transient_service)
        _upsert_preview_environment_variables(preview, transient_service)

        # 4. Trigger deployment on every run, including rebuilds.
        deployment = Deployment.objects.create(
            service=transient_service,
            commit_hash=preview.commit_sha,
            branch=preview.branch_name,
            commit_message=f"SafeDeploy preview for {preview.branch_name}"
        )
        provider_id = str(parent.provider.id) if parent.provider else None
        _dispatch_preview_deployment(deployment, provider_id)
    except Exception as e:
        logger.error(f"Failed to provision preview environment {preview_id}: {e}", exc_info=True)
        try:
            p = PreviewEnvironment.objects.get(id=preview_id)
            p.status = PreviewEnvironment.Status.HEALTH_CHECK_FAILED
            p.error_message = str(e)
            p.save()
        except Exception:
            pass

@shared_task
def run_preview_health_check_job(preview_id: str):
    try:
        from apps.deployments.services.safedeploy.health_checks import perform_health_check
        preview = PreviewEnvironment.objects.get(id=preview_id)
        if not preview.preview_url:
            preview.status = PreviewEnvironment.Status.HEALTH_CHECK_FAILED
            preview.error_message = "No preview URL configured"
            preview.save()
            return
        ok, result = perform_health_check(preview.preview_url)
        if ok:
            preview.status = PreviewEnvironment.Status.READY
        else:
            preview.status = PreviewEnvironment.Status.HEALTH_CHECK_FAILED
            preview.error_message = result.error_message or "Health check returned non-2xx"
        preview.save()
    except Exception as e:
        logger.error(f"Health check failed for preview {preview_id}: {e}", exc_info=True)

@shared_task
def destroy_preview_environment_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)

        # 1. Destroy Transient Service
        transient_service_name = _preview_service_name(preview)
        transient_service = Service.objects.filter(name=transient_service_name, is_preview=True).first()
        if transient_service:
            transient_service.status = Service.Status.DELETION_PENDING
            transient_service.save()
            from apps.deployments.tasks import delete_service_task
            delete_service_task.delay(str(transient_service.id))
            # Allow time for the container to be torn down before destroying the DB.
            # The container deletion is async via Celery; the DB destroy is sync.
            # Without this gap, the next deploy to the same service name will
            # create a fresh DB clone with no production data.
            time.sleep(5)

        # 2. Destroy Database Clone
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
        pass
    except Exception as e:
        logger.error(f"Failed to destroy preview environment {preview_id}: {e}", exc_info=True)
        try:
            p = PreviewEnvironment.objects.get(id=preview_id)
            p.status = PreviewEnvironment.Status.BUILD_FAILED  # TODO: add DESTROY_FAILED status
            p.error_message = str(e)
            p.save()
        except Exception:
            pass


@shared_task
def expire_stale_previews_job():
    from django.utils import timezone
    from apps.deployments.models_safedeploy import PreviewEnvironment
    now = timezone.now()
    expired = PreviewEnvironment.objects.filter(
        expires_at__lt=now,
        status__in=[
            PreviewEnvironment.Status.READY,
            PreviewEnvironment.Status.HEALTH_CHECK_FAILED,
            PreviewEnvironment.Status.TESTS_FAILED,
            PreviewEnvironment.Status.MIGRATION_FAILED,
            PreviewEnvironment.Status.DB_CLONE_FAILED,
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
