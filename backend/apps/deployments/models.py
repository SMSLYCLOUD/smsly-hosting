import uuid
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField
from django.utils.translation import gettext_lazy as _

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class Service(TimeStampedModel):
    """
    Represents a hosted application/service.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    repository_url = models.URLField(help_text="Git repository URL")
    branch = models.CharField(max_length=255, default='main')

    # Build & Run Config
    build_command = models.CharField(max_length=255, blank=True, null=True)
    start_command = models.CharField(max_length=255, blank=True, null=True)
    root_directory = models.CharField(max_length=255, default='/')

    # Network
    internal_port = models.IntegerField(default=8000)
    public_domain = models.CharField(max_length=255, blank=True, null=True, unique=True)
    domain_verified = models.BooleanField(default=False)
    verification_token = models.CharField(max_length=64, blank=True)

    # Resource Limits (Simulated for now)
    cpu_cores = models.DecimalField(max_digits=4, decimal_places=2, default=0.5)
    memory_mb = models.IntegerField(default=512)

    # Auto-Scaling
    min_replicas = models.IntegerField(default=1)
    max_replicas = models.IntegerField(default=1)
    autoscale_cpu_target = models.IntegerField(default=80, help_text="Target CPU utilization percentage")

    # Strategy
    use_blue_green = models.BooleanField(default=False, help_text="Use Blue/Green deployment strategy")

    # Preview Environments
    is_preview = models.BooleanField(default=False)
    parent_service = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='previews')
    pr_number = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.verification_token:
            self.verification_token = f"smsly-verify-{uuid.uuid4().hex[:12]}"
        super().save(*args, **kwargs)

class ComplianceProfile(models.Model):
    """
    Enterprise compliance settings for a service.
    """
    service = models.OneToOneField(Service, on_delete=models.CASCADE, related_name='compliance')
    hipaa_compliant = models.BooleanField(default=False)
    gdpr_compliant = models.BooleanField(default=False)
    soc2_compliant = models.BooleanField(default=False)
    data_residency = models.CharField(max_length=50, default='us-east-1')

    def __str__(self):
        return f"Compliance for {self.service.name}"

class EnvironmentVariable(TimeStampedModel):
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='env_vars')
    key = models.CharField(max_length=255)
    value = EncryptedCharField(max_length=255)
    is_secret = models.BooleanField(default=False)

    class Meta:
        unique_together = ('service', 'key')

    def __str__(self):
        return f"{self.key} ({self.service.name})"

class Deployment(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = 'QUEUED', _('Queued')
        BUILDING = 'BUILDING', _('Building')
        DEPLOYING = 'DEPLOYING', _('Deploying')
        HEALTH_CHECK = 'HEALTH_CHECK', _('Health Check')
        ACTIVE = 'ACTIVE', _('Active')
        FAILED = 'FAILED', _('Failed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='deployments')
    commit_hash = models.CharField(max_length=40)
    commit_message = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )

    build_logs = models.TextField(blank=True)
    runtime_logs_url = models.URLField(blank=True, null=True)

    ai_diagnosis = models.TextField(blank=True, help_text="AI suggested fix for failure")

    # Security
    vulnerability_report = models.JSONField(default=dict, blank=True, help_text="Trivy scan results")

    container_id = models.CharField(max_length=255, blank=True, null=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.service.name} - {self.commit_hash[:7]} ({self.status})"
