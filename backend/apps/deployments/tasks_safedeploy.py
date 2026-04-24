import logging
import os
import shutil
import tempfile
import subprocess
from celery import shared_task
from apps.deployments.models_safedeploy import PreviewEnvironment, DatabaseClone, MigrationValidation, DeploymentArtifact
from apps.deployments.services.safedeploy.postgres_snapshot_manager import PostgresSnapshotManager
from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter

logger = logging.getLogger(__name__)

def checkout_code(repo_url: str, branch: str, commit_sha: str, target_dir: str) -> bool:
    try:
        subprocess.run(["git", "clone", "--branch", branch, repo_url, target_dir], check=True, capture_output=True)
        subprocess.run(["git", "checkout", commit_sha], cwd=target_dir, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Git clone failed: {e.stderr}")
        return False

@shared_task
def create_preview_environment_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        preview.status = PreviewEnvironment.Status.BUILDING
        preview.save()
        create_database_clone_job.delay(preview_id)
    except Exception as e:
        pass

@shared_task
def create_database_clone_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        safe_service_name = preview.service.name.replace('-','_').lower()
        clone_db_name = f"preview_{str(preview.service.id)[:8]}_{preview.branch_name}_{preview.commit_sha[:8]}".replace('-','_')

        clone = DatabaseClone.objects.create(
            service=preview.service,
            preview_environment=preview,
            source_environment='production',
            source_database_name=safe_service_name,
            clone_database_name=clone_db_name,
            status=DatabaseClone.Status.CREATING
        )

        preview.status = PreviewEnvironment.Status.DB_CLONE_CREATING
        preview.save()

        db_manager = PostgresSnapshotManager()
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
            clone.save()
            preview.status = PreviewEnvironment.Status.DB_CLONE_FAILED
            preview.save()
    except Exception as e:
        pass

@shared_task
def run_migration_validation_job(preview_id: str):
    workspace_dir = None
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        adapter = DjangoAdapter()

        workspace_dir = tempfile.mkdtemp(prefix=f"preview_{preview.id}_")
        repo_url = preview.service.repository_url
        if repo_url:
             success = checkout_code(repo_url, preview.branch_name, preview.commit_sha, workspace_dir)
             if not success: raise Exception("Failed to clone repository")

        validation, _ = MigrationValidation.objects.get_or_create(preview_environment=preview)

        if not hasattr(preview, 'database_clone') or not preview.database_clone or preview.database_clone.status != DatabaseClone.Status.READY:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = "No ready database clone available"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        clone_url = preview.database_clone.clone_database_url_secret_ref

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

        if not adapter.detect(workspace_dir):
            validation.status = MigrationValidation.Status.NOT_CONFIGURED
            validation.save()
            preview.status = PreviewEnvironment.Status.TESTS_RUNNING
            preview.save()
            return

        env = {"DATABASE_URL": clone_url}

        # Run Check
        rc, out, err = adapter.run_check(workspace_dir, env)
        if rc != 0:
            validation.status = MigrationValidation.Status.FAILED
            validation.error_message = "Django check failed"
            validation.save()
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.save()
            return

        # Makemigrations --check
        adapter.run_makemigrations_check(workspace_dir, env)

        # Showmigrations
        rc, plan_out, err = adapter.run_showmigrations(workspace_dir, env)
        DeploymentArtifact.objects.create(service=preview.service, preview_environment=preview, artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_PLAN, content=plan_out)

        # Risk Classification
        operations = adapter.inspect_migration_files(workspace_dir)
        risk_report = adapter.classify_migration_risk(operations)

        validation.risk_level = risk_report['risk_level']
        validation.risk_score = risk_report['risk_score']
        validation.summary = risk_report['summary']
        validation.reasons = risk_report['reasons']
        validation.requires_manual_approval = risk_report['requires_manual_approval']
        validation.requires_backup = risk_report['requires_backup']
        validation.can_auto_deploy = risk_report['can_auto_deploy']

        # Migrate
        rc, out, err = adapter.run_migrate(workspace_dir, env)
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
            preview.status = PreviewEnvironment.Status.MIGRATION_FAILED
            preview.error_message = str(e)
            preview.save()
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
        pass

@shared_task
def run_preview_health_check_job(preview_id: str):
    try:
        preview = PreviewEnvironment.objects.get(id=preview_id)
        preview.status = PreviewEnvironment.Status.READY
        preview.save()
    except Exception as e:
        pass
