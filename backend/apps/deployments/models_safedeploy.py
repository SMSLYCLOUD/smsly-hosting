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
    commit_sha = models.CharField(max_length=64, db_index=True)
    preview_url = models.URLField(blank=True, null=True)
    image_tag = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

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

    class AutoDeployPolicy(models.TextChoices):
        NEVER = 'NEVER', 'Never auto-deploy (always requires approval)'
        LOW_RISK_ONLY = 'LOW_RISK_ONLY', 'Auto-deploy for LOW risk only'
        ALWAYS = 'ALWAYS', 'Auto-deploy when can_auto_deploy is True'

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

    auto_deploy_policy = models.CharField(max_length=20, choices=AutoDeployPolicy.choices, default=AutoDeployPolicy.LOW_RISK_ONLY)
    requires_backup = models.BooleanField(default=False)

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
    rejected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_approvals')

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
