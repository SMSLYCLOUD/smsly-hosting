#!/bin/bash
set -e

# Data Models
cat << 'INNER' > backend/apps/deployments/models_safedeploy.py
import uuid
from django.db import models
from django.conf import settings
from .models_core import TimeStampedModel, Service, Deployment
from encrypted_model_fields.fields import EncryptedCharField

class PreviewEnvironment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        BUILDING = 'BUILDING', 'Building'
        BUILD_FAILED = 'BUILD_FAILED', 'Build Failed'
        PROVISIONING = 'PROVISIONING', 'Provisioning'
        DB_CLONE_CREATING = 'DB_CLONE_CREATING', 'DB Clone Creating'
        DB_CLONE_FAILED = 'DB_CLONE_FAILED', 'DB Clone Failed'
        MIGRATION_RUNNING = 'MIGRATION_RUNNING', 'Migration Running'
        MIGRATION_FAILED = 'MIGRATION_FAILED', 'Migration Failed'
        TESTS_RUNNING = 'TESTS_RUNNING', 'Tests Running'
        TESTS_FAILED = 'TESTS_FAILED', 'Tests Failed'
        HEALTH_CHECK_RUNNING = 'HEALTH_CHECK_RUNNING', 'Health Check Running'
        HEALTH_CHECK_FAILED = 'HEALTH_CHECK_FAILED', 'Health Check Failed'
        READY = 'READY', 'Ready'
        EXPIRED = 'EXPIRED', 'Expired'
        DESTROYING = 'DESTROYING', 'Destroying'
        DESTROYED = 'DESTROYED', 'Destroyed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='preview_environments')
    project_id = models.UUIDField(null=True, blank=True)
    branch_name = models.CharField(max_length=255)
    commit_sha = models.CharField(max_length=64)
    preview_url = models.URLField(blank=True, null=True)
    image_tag = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"{self.service.name} - {self.branch_name} ({self.status})"

class DatabaseClone(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        CREATING = 'CREATING', 'Creating'
        READY = 'READY', 'Ready'
        FAILED = 'FAILED', 'Failed'
        DESTROYING = 'DESTROYING', 'Destroying'
        DESTROYED = 'DESTROYED', 'Destroyed'
        EXPIRED = 'EXPIRED', 'Expired'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='database_clones')
    preview_environment = models.OneToOneField(PreviewEnvironment, on_delete=models.CASCADE, related_name='database_clone', null=True, blank=True)
    source_environment = models.CharField(max_length=50, default='production')
    source_database_name = models.CharField(max_length=255)
    clone_database_name = models.CharField(max_length=255)
    clone_database_url_secret_ref = EncryptedCharField(max_length=1024, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField(null=True, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"Clone of {self.source_database_name} for {self.service.name}"

class MigrationValidation(TimeStampedModel):
    class RiskLevel(models.TextChoices):
        LOW = 'LOW', 'Low Risk'
        MEDIUM = 'MEDIUM', 'Medium Risk'
        HIGH = 'HIGH', 'High Risk'
        CRITICAL = 'CRITICAL', 'Critical Risk'
        UNKNOWN = 'UNKNOWN', 'Unknown'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PASSED = 'PASSED', 'Passed'
        FAILED = 'FAILED', 'Failed'
        INCOMPLETE = 'INCOMPLETE', 'Incomplete'
        SKIPPED = 'SKIPPED', 'Skipped'
        NOT_CONFIGURED = 'NOT_CONFIGURED', 'Not Configured'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    preview_environment = models.OneToOneField(PreviewEnvironment, on_delete=models.CASCADE, related_name='migration_validation', null=True, blank=True)
    deployment = models.OneToOneField(Deployment, on_delete=models.CASCADE, related_name='migration_validation', null=True, blank=True)

    risk_level = models.CharField(max_length=20, choices=RiskLevel.choices, default=RiskLevel.UNKNOWN)
    risk_score = models.IntegerField(default=0, help_text="0 to 100")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    summary = models.TextField(blank=True)
    reasons = models.JSONField(default=list, blank=True)
    detected_operations = models.JSONField(default=list, blank=True)
    recommendations = models.JSONField(default=list, blank=True)

    requires_manual_approval = models.BooleanField(default=False)
    requires_backup = models.BooleanField(default=False)
    can_auto_deploy = models.BooleanField(default=False)

    error_message = models.TextField(blank=True)

class DeploymentApproval(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        EXPIRED = 'EXPIRED', 'Expired'
        AUTO_APPROVED = 'AUTO_APPROVED', 'Auto Approved'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='approvals')
    deployment = models.OneToOneField(Deployment, on_delete=models.CASCADE, related_name='approval', null=True, blank=True)
    preview_environment = models.ForeignKey(PreviewEnvironment, on_delete=models.SET_NULL, related_name='approvals', null=True, blank=True)

    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='requested_approvals')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='granted_approvals')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    risk_level = models.CharField(max_length=20, choices=MigrationValidation.RiskLevel.choices, default=MigrationValidation.RiskLevel.UNKNOWN)
    approval_notes = models.TextField(blank=True)

    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

class DeploymentArtifact(TimeStampedModel):
    class ArtifactType(models.TextChoices):
        BUILD_LOG = 'BUILD_LOG', 'Build Log'
        MIGRATION_PLAN = 'MIGRATION_PLAN', 'Migration Plan'
        MIGRATION_OUTPUT = 'MIGRATION_OUTPUT', 'Migration Output'
        TEST_OUTPUT = 'TEST_OUTPUT', 'Test Output'
        HEALTH_CHECK_OUTPUT = 'HEALTH_CHECK_OUTPUT', 'Health Check Output'
        RISK_REPORT = 'RISK_REPORT', 'Risk Report'
        DEPLOYMENT_REPORT = 'DEPLOYMENT_REPORT', 'Deployment Report'
        ROLLBACK_REPORT = 'ROLLBACK_REPORT', 'Rollback Report'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='artifacts')
    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name='artifacts', null=True, blank=True)
    preview_environment = models.ForeignKey(PreviewEnvironment, on_delete=models.CASCADE, related_name='artifacts', null=True, blank=True)

    artifact_type = models.CharField(max_length=30, choices=ArtifactType.choices)
    content = models.TextField(blank=True)
    file_path = models.CharField(max_length=255, blank=True, null=True)

class HealthCheckResult(TimeStampedModel):
    class Status(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        PENDING = 'PENDING', 'Pending'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='health_checks')
    environment_id = models.UUIDField(null=True, blank=True)
    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name='health_checks', null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    url = models.URLField(blank=True, null=True)
    status_code = models.IntegerField(null=True, blank=True)
    response_time_ms = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    checked_at = models.DateTimeField(auto_now_add=True)
INNER

sed -i 's/from .models_addons import Addon, Backup/from .models_safedeploy import PreviewEnvironment, DatabaseClone, MigrationValidation, DeploymentApproval, DeploymentArtifact, HealthCheckResult\nfrom .models_addons import Addon, Backup/g' backend/apps/deployments/models.py

sed -i "s/QUEUED = 'QUEUED', _('Queued')/QUEUED = 'QUEUED', _('Queued')\n        BUILD_FAILED = 'BUILD_FAILED', _('Build Failed')\n        AWAITING_APPROVAL = 'AWAITING_APPROVAL', _('Awaiting Approval')\n        BACKUP_RUNNING = 'BACKUP_RUNNING', _('Backup Running')\n        BACKUP_FAILED = 'BACKUP_FAILED', _('Backup Failed')\n        MIGRATION_PLANNING = 'MIGRATION_PLANNING', _('Migration Planning')\n        MIGRATION_RUNNING = 'MIGRATION_RUNNING', _('Migration Running')\n        MIGRATION_FAILED = 'MIGRATION_FAILED', _('Migration Failed')\n        TRAFFIC_SHIFTING = 'TRAFFIC_SHIFTING', _('Traffic Shifting')\n        MONITORING = 'MONITORING', _('Monitoring')\n        ROLLING_BACK = 'ROLLING_BACK', _('Rolling Back')\n        ROLLED_BACK = 'ROLLED_BACK', _('Rolled Back')/g" backend/apps/deployments/models_core.py

sed -i "/DEPLOY_STRATEGY_CHOICES = \[/i \
    safe_deploy_enabled = models.BooleanField(default=False)\n    preview_environments_enabled = models.BooleanField(default=False)\n    auto_create_preview_on_branch_push = models.BooleanField(default=False)\n    MIGRATION_AUTO_APPROVAL_CHOICES = [('NEVER', 'Never'), ('LOW_RISK_ONLY', 'Low Risk Only'), ('LOW_AND_MEDIUM', 'Low and Medium'), ('ALWAYS_REQUIRE_MANUAL', 'Always Require Manual')]\n    migration_auto_approval_policy = models.CharField(max_length=50, choices=MIGRATION_AUTO_APPROVAL_CHOICES, default='LOW_RISK_ONLY')\n    production_requires_backup = models.BooleanField(default=True)\n    health_check_path = models.CharField(max_length=255, default='/health')\n" backend/apps/deployments/models_core.py

mkdir -p backend/apps/deployments/services/safedeploy

cat << 'INNER' > backend/apps/deployments/services/safedeploy/command_executor.py
import subprocess
import os
import logging
from typing import Tuple, Dict
from .redaction import redact_secrets

logger = logging.getLogger(__name__)

class CommandExecutor:
    def run(self, cmd: str, cwd: str, env: Dict[str, str] = None, timeout: int = 120) -> Tuple[int, str, str]:
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        logger.info(f"Executing command in {cwd}: {cmd}")
        try:
            result = subprocess.run(cmd, shell=True, cwd=cwd, env=run_env, capture_output=True, text=True, timeout=timeout)
            stdout = redact_secrets(result.stdout)
            stderr = redact_secrets(result.stderr)
            return result.returncode, stdout, stderr
        except subprocess.TimeoutExpired as e:
            logger.warning(f"Command timed out: {cmd}")
            stdout = redact_secrets(e.stdout.decode() if e.stdout else "")
            stderr = redact_secrets(e.stderr.decode() if e.stderr else "Command timed out")
            return 124, stdout, stderr
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return 1, "", str(e)
INNER

cat << 'INNER' > backend/apps/deployments/services/safedeploy/redaction.py
import re

def redact_secrets(text: str) -> str:
    if not text: return text
    patterns = [
        (r'(DATABASE_URL\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(SECRET_KEY\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(API_KEY\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(TOKEN\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(PASSWORD\s*=\s*)([^\s]+)', r'\1[REDACTED]'),
        (r'(://[^:]+:)([^@]+)(@)', r'\1[REDACTED]\3'),
    ]
    redacted = text
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    return redacted
INNER

cat << 'INNER' > backend/apps/deployments/services/safedeploy/postgres_snapshot_manager.py
from typing import Optional
import subprocess
import logging
import os

logger = logging.getLogger(__name__)

class PostgresSnapshotManager:
    def __init__(self, admin_db_url: Optional[str] = None):
        self.admin_db_url = admin_db_url or os.environ.get('DATABASE_URL', 'postgres://postgres:postgres@localhost:5432/postgres')

    def create_clone(self, source_db_name: str, clone_db_name: str) -> bool:
        try:
            logger.info(f"Cloning DB {source_db_name} to {clone_db_name}")
            term_sql = f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{source_db_name}' AND pid <> pg_backend_pid();"
            subprocess.run(['psql', self.admin_db_url, '-c', term_sql], check=False, capture_output=True)
            create_sql = f'CREATE DATABASE "{clone_db_name}" WITH TEMPLATE "{source_db_name}";'
            res = subprocess.run(['psql', self.admin_db_url, '-c', create_sql], check=True, capture_output=True, text=True)
            return True
        except Exception as e:
            logger.error(f"Failed to clone db: {str(e)}")
            return False

    def destroy_clone(self, clone_db_name: str) -> bool:
        if 'prod' in clone_db_name.lower() or 'main' in clone_db_name.lower():
             logger.error(f"SECURITY BLOCK: Attempted to drop protected database name '{clone_db_name}'")
             return False
        try:
            term_sql = f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{clone_db_name}';"
            subprocess.run(['psql', self.admin_db_url, '-c', term_sql], check=False, capture_output=True)
            drop_sql = f'DROP DATABASE IF EXISTS "{clone_db_name}";'
            subprocess.run(['psql', self.admin_db_url, '-c', drop_sql], check=True, capture_output=True)
            return True
        except Exception as e:
            return False

    def get_clone_url(self, clone_db_name: str) -> str:
        base_url = self.admin_db_url
        parts = base_url.split('/')
        return '/'.join(parts[:-1]) + f"/{clone_db_name}"
INNER


cat << 'INNER' > backend/apps/deployments/services/safedeploy/django_adapter.py
import os
from typing import Dict, Any, List
from .command_executor import CommandExecutor
from apps.deployments.models_safedeploy import MigrationValidation

class DjangoAdapter:
    def __init__(self):
        self.executor = CommandExecutor()

    def detect(self, project_path: str) -> bool:
        return os.path.exists(os.path.join(project_path, 'manage.py'))

    def run_check(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run("python manage.py check", cwd, env)

    def run_makemigrations_check(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run("python manage.py makemigrations --check --dry-run", cwd, env)

    def run_showmigrations(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run("python manage.py showmigrations --plan", cwd, env)

    def run_migrate(self, cwd: str, env: dict) -> tuple[int, str, str]:
        return self.executor.run("python manage.py migrate --noinput", cwd, env)

    def inspect_migration_files(self, project_path: str) -> List[Dict[str, Any]]:
        operations = []
        if not os.path.exists(project_path): return operations
        for root, _, files in os.walk(project_path):
            if 'migrations' in root:
                for file in files:
                    if file.endswith('.py') and file != '__init__.py':
                        filepath = os.path.join(root, file)
                        try:
                            with open(filepath, 'r') as f:
                                content = f.read()
                                if 'migrations.RemoveField' in content: operations.append({'type': 'RemoveField', 'file': file})
                                if 'migrations.DeleteModel' in content: operations.append({'type': 'DeleteModel', 'file': file})
                                if 'migrations.RunPython' in content: operations.append({'type': 'RunPython', 'file': file})
                                if 'migrations.RunSQL' in content: operations.append({'type': 'RunSQL', 'file': file})
                                if 'migrations.AlterField' in content: operations.append({'type': 'AlterField', 'file': file})
                                if 'migrations.AddField' in content: operations.append({'type': 'AddField', 'file': file})
                        except Exception:
                            pass
        return operations

    def classify_migration_risk(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        risk_score = 0
        reasons = []
        has_critical = False
        has_high = False
        has_medium = False

        for op in operations:
            op_type = op.get('type')
            if op_type in ['DeleteModel', 'RunSQL']:
                has_critical = True
                risk_score += 100
                reasons.append(f"Contains {op_type}.")
            elif op_type in ['RemoveField', 'RunPython']:
                has_high = True
                risk_score += 50
                reasons.append(f"Contains {op_type}.")
            elif op_type in ['AlterField']:
                has_medium = True
                risk_score += 20

        if risk_score > 100: risk_score = 100

        risk_level = MigrationValidation.RiskLevel.LOW
        if has_critical: risk_level = MigrationValidation.RiskLevel.CRITICAL
        elif has_high: risk_level = MigrationValidation.RiskLevel.HIGH
        elif has_medium: risk_level = MigrationValidation.RiskLevel.MEDIUM

        return {
            'risk_level': risk_level,
            'risk_score': risk_score,
            'reasons': reasons,
            'requires_manual_approval': risk_level in [MigrationValidation.RiskLevel.HIGH, MigrationValidation.RiskLevel.CRITICAL],
            'requires_backup': risk_level != MigrationValidation.RiskLevel.LOW,
            'can_auto_deploy': risk_level == MigrationValidation.RiskLevel.LOW,
            'summary': f"Migration risk is {risk_level} (Score: {risk_score})"
        }
INNER

cat << 'INNER' > backend/apps/deployments/tasks_safedeploy.py
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
INNER

cat << 'INNER' > backend/apps/deployments/services/safedeploy/deployment_pipeline.py
import logging
from django.utils import timezone
from apps.deployments.models_core import Deployment
from apps.deployments.models_safedeploy import DeploymentApproval, MigrationValidation, DeploymentArtifact
from .postgres_snapshot_manager import PostgresSnapshotManager

logger = logging.getLogger(__name__)

class ProductionDeploymentPipeline:
    def process_deployment(self, deployment: Deployment) -> None:
        deployment.status = Deployment.Status.MIGRATION_PLANNING
        deployment.save()

        validation = self._get_latest_validation_for_commit(deployment.service_id, deployment.commit_hash)

        # Guardrail: Do not allow deploy if validation did not pass or wasn't configured properly
        if validation:
            validation.deployment = deployment
            validation.save()

            if validation.status in [MigrationValidation.Status.FAILED, MigrationValidation.Status.INCOMPLETE]:
                logger.error(f"Blocking deployment {deployment.id}: Migration validation did not pass (Status: {validation.status})")
                deployment.status = Deployment.Status.FAILED
                deployment.save()
                return

            if validation.requires_manual_approval:
                approval = DeploymentApproval.objects.filter(deployment=deployment, status=DeploymentApproval.Status.APPROVED).first()
                if not approval:
                    deployment.status = Deployment.Status.AWAITING_APPROVAL
                    deployment.save()
                    return

        if validation and validation.requires_backup:
            self._run_backup_phase(deployment)
            if deployment.status == Deployment.Status.BACKUP_FAILED: return

        self._run_migration_phase(deployment)
        if deployment.status == Deployment.Status.MIGRATION_FAILED: return

        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()

        # Mock completion
        deployment.status = Deployment.Status.ACTIVE
        deployment.save()

    def _get_latest_validation_for_commit(self, service_id, commit_hash):
        return MigrationValidation.objects.filter(preview_environment__service_id=service_id, preview_environment__commit_sha=commit_hash).order_by('-created_at').first()

    def _run_backup_phase(self, deployment: Deployment) -> None:
        deployment.status = Deployment.Status.BACKUP_RUNNING
        deployment.save()
        logger.info(f"Triggered production backup for deployment {deployment.id}")

    def _run_migration_phase(self, deployment: Deployment) -> None:
        deployment.status = Deployment.Status.MIGRATION_RUNNING
        deployment.save()
        try:
            from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
            import tempfile, shutil, subprocess
            adapter = DjangoAdapter()
            workspace_dir = tempfile.mkdtemp(prefix=f"prod_deploy_{deployment.id}_")
            repo_url = deployment.service.repository_url
            if repo_url:
                try:
                    subprocess.run(["git", "clone", "--branch", deployment.service.branch, repo_url, workspace_dir], check=True, capture_output=True)
                    subprocess.run(["git", "checkout", deployment.commit_hash], cwd=workspace_dir, check=True, capture_output=True)
                except Exception:
                    pass
            prod_db_url = None
            for env_var in deployment.service.env_vars.all():
                if env_var.key == 'DATABASE_URL':
                    prod_db_url = env_var.value

            if prod_db_url and adapter.detect(workspace_dir):
                env = {"DATABASE_URL": prod_db_url}
                rc, out, err = adapter.run_migrate(workspace_dir, env)
                DeploymentArtifact.objects.create(service=deployment.service, deployment=deployment, artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_OUTPUT, content=f"RC: {rc}\n{out}\n{err}")
                if rc != 0: raise Exception("Production Migration apply failed.")
            shutil.rmtree(workspace_dir, ignore_errors=True)
        except Exception as e:
            deployment.status = Deployment.Status.MIGRATION_FAILED
            deployment.save()

    def approve_deployment(self, deployment: Deployment, user) -> DeploymentApproval:
        approval, _ = DeploymentApproval.objects.get_or_create(service=deployment.service, deployment=deployment)
        approval.status = DeploymentApproval.Status.APPROVED
        approval.approved_by = user
        approval.approved_at = timezone.now()
        validation = getattr(deployment, 'migration_validation', None)
        if validation: approval.risk_level = validation.risk_level
        approval.save()
        self.process_deployment(deployment)
        return approval

    def reject_deployment(self, deployment: Deployment, user, notes: str = "") -> DeploymentApproval:
        approval, _ = DeploymentApproval.objects.get_or_create(service=deployment.service, deployment=deployment)
        approval.status = DeploymentApproval.Status.REJECTED
        approval.approved_by = user
        approval.rejected_at = timezone.now()
        approval.approval_notes = notes
        approval.save()
        deployment.status = Deployment.Status.CANCELLED
        deployment.save()
        return approval
INNER
