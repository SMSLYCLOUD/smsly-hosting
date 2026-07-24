import uuid

from django.conf import settings
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

from .core import Deployment, Service, TimeStampedModel


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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='preview_environments')  # type: ignore[var-annotated]
    project_id = models.UUIDField(null=True, blank=True)  # type: ignore[var-annotated]
    branch_name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    commit_sha = models.CharField(max_length=64, db_index=True)  # type: ignore[var-annotated]
    preview_url = models.URLField(blank=True, null=True)  # type: ignore[var-annotated]
    image_tag = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)  # type: ignore[var-annotated]
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)  # type: ignore[var-annotated]
    expires_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    error_message = models.TextField(blank=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ['-created_at']
        unique_together = (('service', 'branch_name', 'commit_sha'),)

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='database_clones')  # type: ignore[var-annotated]
    preview_environment = models.OneToOneField(PreviewEnvironment, on_delete=models.CASCADE, related_name='database_clone', null=True, blank=True)  # type: ignore[var-annotated]
    source_environment = models.CharField(max_length=50, default='production')  # type: ignore[var-annotated]
    source_database_name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    clone_database_name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    clone_database_url_secret_ref = EncryptedCharField(max_length=1024, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)  # type: ignore[var-annotated]
    expires_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    size_bytes = models.BigIntegerField(null=True, blank=True)  # type: ignore[var-annotated]
    error_message = models.TextField(blank=True)  # type: ignore[var-annotated]

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

    class AutoDeployPolicy(models.TextChoices):
        NEVER = 'NEVER', 'Never auto-deploy (always requires approval)'
        LOW_RISK_ONLY = 'LOW_RISK_ONLY', 'Auto-deploy for LOW risk only'
        ALWAYS = 'ALWAYS', 'Auto-deploy when can_auto_deploy is True'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    preview_environment = models.OneToOneField(PreviewEnvironment, on_delete=models.CASCADE, related_name='migration_validation', null=True, blank=True)  # type: ignore[var-annotated]
    deployment = models.OneToOneField(Deployment, on_delete=models.CASCADE, related_name='migration_validation', null=True, blank=True)  # type: ignore[var-annotated]

    risk_level = models.CharField(max_length=20, choices=RiskLevel.choices, default=RiskLevel.UNKNOWN)  # type: ignore[var-annotated]
    risk_score = models.IntegerField(default=0, help_text="0 to 100")  # type: ignore[var-annotated]
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)  # type: ignore[var-annotated]
    summary = models.TextField(blank=True)  # type: ignore[var-annotated]
    reasons = models.JSONField(default=list, blank=True)  # type: ignore[var-annotated]
    detected_operations = models.JSONField(default=list, blank=True)  # type: ignore[var-annotated]
    recommendations = models.JSONField(default=list, blank=True)  # type: ignore[var-annotated]

    auto_deploy_policy = models.CharField(max_length=20, choices=AutoDeployPolicy.choices, default=AutoDeployPolicy.LOW_RISK_ONLY)  # type: ignore[var-annotated]
    requires_backup = models.BooleanField(default=False)  # type: ignore[var-annotated]

    error_message = models.TextField(blank=True)  # type: ignore[var-annotated]

class DeploymentApproval(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'
        EXPIRED = 'EXPIRED', 'Expired'
        AUTO_APPROVED = 'AUTO_APPROVED', 'Auto Approved'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='approvals')  # type: ignore[var-annotated]
    deployment = models.OneToOneField(Deployment, on_delete=models.CASCADE, related_name='approval', null=True, blank=True)  # type: ignore[var-annotated]
    preview_environment = models.ForeignKey(PreviewEnvironment, on_delete=models.SET_NULL, related_name='approvals', null=True, blank=True)  # type: ignore[var-annotated]

    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='requested_approvals')  # type: ignore[var-annotated]
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='granted_approvals')  # type: ignore[var-annotated]
    rejected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_approvals')  # type: ignore[var-annotated]

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)  # type: ignore[var-annotated]
    risk_level = models.CharField(max_length=20, choices=MigrationValidation.RiskLevel.choices, default=MigrationValidation.RiskLevel.UNKNOWN)  # type: ignore[var-annotated]
    approval_notes = models.TextField(blank=True)  # type: ignore[var-annotated]

    approved_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    rejected_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='artifacts')  # type: ignore[var-annotated]
    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name='artifacts', null=True, blank=True)  # type: ignore[var-annotated]
    preview_environment = models.ForeignKey(PreviewEnvironment, on_delete=models.CASCADE, related_name='artifacts', null=True, blank=True)  # type: ignore[var-annotated]

    artifact_type = models.CharField(max_length=30, choices=ArtifactType.choices)  # type: ignore[var-annotated]
    content = models.TextField(blank=True)  # type: ignore[var-annotated]
    file_path = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    is_archived = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Soft-delete flag: when True the row is hidden from "
                  "default querysets but is preserved for audit.",
    )

class HealthCheckResult(TimeStampedModel):
    class Status(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        PENDING = 'PENDING', 'Pending'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='health_checks')  # type: ignore[var-annotated]
    environment_id = models.UUIDField(null=True, blank=True)  # type: ignore[var-annotated]
    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name='health_checks', null=True, blank=True)  # type: ignore[var-annotated]
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)  # type: ignore[var-annotated]
    url = models.URLField(blank=True, null=True)  # type: ignore[var-annotated]
    status_code = models.IntegerField(null=True, blank=True)  # type: ignore[var-annotated]
    response_time_ms = models.IntegerField(null=True, blank=True)  # type: ignore[var-annotated]
    error_message = models.TextField(blank=True)  # type: ignore[var-annotated]
    checked_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
