import logging
import os
import shutil
import tempfile
import subprocess
from celery import shared_task
from apps.deployments.models_safedeploy import PreviewEnvironment, DatabaseClone, MigrationValidation, DeploymentArtifact
from apps.deployments.services.safedeploy.postgres_snapshot_manager import PostgresSnapshotManager
from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
from apps.deployments.models_core import Service, EnvironmentVariable, Deployment
from apps.deployments.models_addons import Addon
logger = logging.getLogger(__name__)

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
        except:
            pass

@shared_task
def create_database_clone_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)

        # Find the service's PostgreSQL addon to determine the source database name
        pg_addon = Addon.objects.filter(
            service=preview.service,
            addon_type=Addon.Type.POSTGRES,
        ).first()

        if not pg_addon:
            logger.info("No PostgreSQL addon for service %s, skipping DB clone", preview.service.id)
            preview.status = PreviewEnvironment.Status.MIGRATION_RUNNING
            preview.save()
            run_migration_validation_job.delay(preview_id)
            return

        # Extract the actual database name from the addon's connection URL
        from urllib.parse import urlparse
        parsed = urlparse(pg_addon.connection_url)
        source_db_name = parsed.path.lstrip('/') if parsed.path else None
        if not source_db_name:
            logger.error("Could not determine database name from addon %s URL for service %s",
                         pg_addon.id, preview.service.id)
            preview.status = PreviewEnvironment.Status.DB_CLONE_FAILED
            preview.error_message = "Could not determine source database name"
            preview.save()
            return

        clone_db_name = f"preview_{source_db_name[:20]}_{preview.branch_name}_{preview.commit_sha[:8]}".replace('-', '_').replace('/', '_').replace('.', '_')
        # Ensure only valid PostgreSQL identifier characters
        clone_db_name = ''.join(c if c.isalnum() or c == '_' else '_' for c in clone_db_name)  

        clone = DatabaseClone.objects.create(
            service=preview.service,
            preview_environment=preview,
            source_environment='production',
            source_database_name=source_db_name,
            clone_database_name=clone_db_name,
            status=DatabaseClone.Status.CREATING
        )

        preview.status = PreviewEnvironment.Status.DB_CLONE_CREATING
        preview.save()

        db_manager = PostgresSnapshotManager(admin_db_url=pg_addon.connection_url)
        success = db_manager.create_clone(clone.source_database_name, clone.clone_database_name)

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
    except Exception as e:
        logger.error(f"Error in create_database_clone_job: {e}", exc_info=True)
        try:
            p = PreviewEnvironment.objects.get(id=preview_id)
            p.status = PreviewEnvironment.Status.DB_CLONE_FAILED
            p.error_message = str(e)
            p.save()
        except:
            pass

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
        validation.requires_manual_approval = risk_report['requires_manual_approval']
        validation.requires_backup = risk_report['requires_backup']
        validation.can_auto_deploy = risk_report['can_auto_deploy']

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
        run_preview_health_check_job.delay(preview_id)
    except Exception as e:
        logger.error(f"Error in run_preview_tests_job for {preview_id}: {e}", exc_info=True)

@shared_task
def run_preview_health_check_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        parent = preview.service

        # 1. Create Transient Service
        transient_service_name = f"preview-{preview.id.hex[:8]}"
        transient_service, created = Service.objects.get_or_create(
            name=transient_service_name,
            defaults={
                'owner': parent.owner,
                'project': parent.project,
                'repo_url': parent.repo_url,
                'branch': preview.branch_name,
                'parent_service': parent,
                'is_preview': True,
                'public_domain': preview.preview_url.replace("https://", "").replace("http://", "") if preview.preview_url else "",
                'custom_domains': [],
                'provider': parent.provider,
                'server': parent.server,
                'install_command': parent.install_command,
                'build_command': parent.build_command,
                'start_command': parent.start_command,
                'root_directory': parent.root_directory,
                'base_directory': parent.base_directory,
                'env_type': parent.env_type,
            }
        )

        if created:
            # 2. Copy Environment Variables
            for env in parent.env_vars.all():
                EnvironmentVariable.objects.create(
                    service=transient_service,
                    key=env.key,
                    value=env.value,
                    is_build_variable=env.is_build_variable,
                    is_locked=env.is_locked
                )
            
            # Inject isolated preview variables (DATABASE_URL, REDIS_PREFIX, etc.)
            from apps.deployments.services.safedeploy.branch_preview_manager import BranchPreviewManager
            preview_vars = BranchPreviewManager().inject_preview_environment_variables(preview)
            for k, v in preview_vars.items():
                env_obj, _ = EnvironmentVariable.objects.get_or_create(service=transient_service, key=k)
                env_obj.value = v
                env_obj.save()

            # 3. Duplicate Addons
            for addon in parent.addons.all():
                new_addon = Addon.objects.create(
                    service=transient_service,
                    project=transient_service.project,
                    name=f"{addon.name}-preview-{preview.id.hex[:6]}",
                    addon_type=addon.addon_type,
                    status=Addon.Status.PROVISIONING
                )
                try:
                    db_clone = preview.database_clone
                except DatabaseClone.DoesNotExist:
                    db_clone = None

                if addon.addon_type == Addon.Type.POSTGRES and db_clone:
                    # Link Postgres to the cloned database instead of provisioning a fresh one
                    if db_clone.status == DatabaseClone.Status.READY:
                        new_addon.connection_url = db_clone.clone_database_url_secret_ref
                        new_addon.status = Addon.Status.ACTIVE
                        new_addon.save()
                else:
                    from apps.deployments.tasks import provision_addon_task
                    provision_addon_task.delay(str(new_addon.id))

            # 4. Trigger Deployment
            deployment = Deployment.objects.create(
                service=transient_service,
                commit_hash=preview.commit_sha,
                commit_message=f"SafeDeploy preview for {preview.branch_name}",
                triggered_by='safe_deploy'
            )
            provider_id = str(parent.provider.id) if parent.provider else None
            from apps.deployments.tasks import enqueue_smart_deploy_task
            enqueue_smart_deploy_task(str(deployment.id), provider_id)

        preview.status = PreviewEnvironment.Status.READY
        preview.save()
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
def destroy_preview_environment_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        
        # 1. Destroy Transient Service
        transient_service_name = f"preview-{preview.id.hex[:8]}"
        transient_service = Service.objects.filter(name=transient_service_name, is_preview=True).first()
        if transient_service:
            transient_service.status = Service.Status.DELETING
            transient_service.save()
            from apps.deployments.tasks import delete_service_task
            delete_service_task.delay(str(transient_service.id))

        # 2. Destroy Database Clone
        try:
            db_clone = preview.database_clone
        except DatabaseClone.DoesNotExist:
            db_clone = None

        if db_clone:
            db_manager = PostgresSnapshotManager()
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

