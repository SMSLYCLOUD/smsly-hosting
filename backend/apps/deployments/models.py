"""Models module."""
import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from encrypted_model_fields.fields import EncryptedCharField
from django.utils.translation import gettext_lazy as _
from apps.cloud.models import CloudProvider

# Import AuditLog explicitly to register it with the app
from .models_audit import AuditLog
from .models_cron import CronJob
from .models_storage import Volume  # Add this
from .api_token_auth import APIToken  # CLI token auth
from .models_servers import ManagedServer  # Multi-server management


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Region(models.Model):
    """
    Physical deployment regions (e.g. us-east-1, eu-central-1).
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    provider = models.CharField(max_length=50, default='aws')
    country_code = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    city = models.CharField(max_length=100)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.provider})"


class Service(TimeStampedModel):
    """
    Represents a hosted application/service.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)

    # Provider Integration
    provider = models.ForeignKey(
        CloudProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services')

    # Source Config
    repository_url = models.URLField(
        help_text="Git repository URL", blank=True, null=True)
    branch = models.CharField(max_length=255, default='main')

    # Deployment Config
    DEPLOY_TYPE_CHOICES = [
        ('GIT', 'Git Repository'),
        ('DOCKER', 'Docker Image'),
        ('UPLOAD', 'File Upload'),
        ('TEMPLATE', 'Predefined Template'),
    ]
    deploy_type = models.CharField(
        max_length=20,
        choices=DEPLOY_TYPE_CHOICES,
        default='GIT')
    docker_image = models.CharField(max_length=255, blank=True, null=True)

    owner = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services')

    # Build & Run Config
    build_command = models.CharField(max_length=255, blank=True, null=True)
    start_command = models.CharField(max_length=255, blank=True, null=True)
    root_directory = models.CharField(max_length=255, default='/')

    # Network
    internal_port = models.IntegerField(default=8000)
    public_domain = models.CharField(
        max_length=255, blank=True, null=True, unique=True)
    domain_verified = models.BooleanField(default=False)
    verification_token = models.CharField(max_length=64, blank=True)

    # Resource Limits (Simulated for now)
    cpu_cores = models.DecimalField(
        max_digits=4, decimal_places=2, default=0.5)
    memory_mb = models.IntegerField(default=512)

    # Auto-Scaling
    min_replicas = models.IntegerField(
        default=1, validators=[MinValueValidator(1)])
    max_replicas = models.IntegerField(
        default=1, validators=[MinValueValidator(1)])
    autoscale_cpu_target = models.IntegerField(
        default=80, help_text="Target CPU utilization percentage (HPA)")
    vpa_enabled = models.BooleanField(
        default=False, help_text="Enable Vertical Pod Autoscaling (VPA)")

    # Multi-Region
    regions = models.ManyToManyField(
        Region,
        blank=True,
        related_name='services',
        help_text="Regions to deploy this service to")
    primary_region = models.ForeignKey(
        Region,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='primary_services')

    # Deployment Strategy
    DEPLOY_STRATEGY_CHOICES = [
        ('ROLLING', 'Rolling Update'),
        ('BLUE_GREEN', 'Blue/Green'),
        ('CANARY', 'Canary'),
    ]
    deploy_strategy = models.CharField(
        max_length=20,
        choices=DEPLOY_STRATEGY_CHOICES,
        default='ROLLING',
        help_text="Deployment strategy for this service")
    canary_percentage = models.IntegerField(
        default=10,
        help_text="Percentage of traffic routed to canary (1-100)")

    # Legacy compat
    use_blue_green = models.BooleanField(
        default=False, help_text="Deprecated: use deploy_strategy instead")

    # Preview Environments
    is_preview = models.BooleanField(default=False)
    parent_service = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='previews')
    pr_number = models.IntegerField(null=True, blank=True)

    # Coolify Integration
    coolify_uuid = models.CharField(max_length=64, blank=True, null=True, unique=True,
                                    help_text="UUID of the application in Coolify")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if (
            self.min_replicas is not None
            and self.max_replicas is not None
            and self.max_replicas < self.min_replicas
        ):
            raise ValidationError({
                'max_replicas': 'max_replicas must be greater than or equal to min_replicas.'
            })

    @property
    def service_url(self):
        """Railway-style auto-generated URL for the service."""
        return f"https://{self.public_domain}" if self.public_domain else None

    def save(self, *args, **kwargs):
        import re
        if not self.verification_token:
            self.verification_token = f"smsly-verify-{uuid.uuid4().hex[:12]}"

        # Auto-generate Railway-style subdomain if not set
        if not self.public_domain:
            slug = re.sub(r'[^a-z0-9]+', '-', self.name.lower()).strip('-')
            short_id = str(self.id)[:6] if self.id else uuid.uuid4().hex[:6]
            self.public_domain = f"{slug}-{short_id}.cloud.smsly.cloud"

        self.full_clean()  # Enforce validation (e.g. max_length)
        super().save(*args, **kwargs)


class ComplianceProfile(models.Model):
    """
    Enterprise compliance settings for a service.
    """
    service = models.OneToOneField(
        Service,
        on_delete=models.CASCADE,
        related_name='compliance')
    hipaa_compliant = models.BooleanField(default=False)
    gdpr_compliant = models.BooleanField(default=False)
    soc2_compliant = models.BooleanField(default=False)
    data_residency = models.CharField(max_length=50, default='us-east-1')

    def __str__(self):
        return f"Compliance for {self.service.name}"


class EnvironmentVariable(TimeStampedModel):
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='env_vars')
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
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='deployments')
    commit_hash = models.CharField(max_length=40)
    commit_message = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )

    build_logs = models.TextField(blank=True)
    runtime_logs_url = models.URLField(blank=True, null=True)

    ai_diagnosis = models.TextField(
        blank=True, help_text="AI suggested fix for failure")

    # Security
    vulnerability_report = models.JSONField(
        default=dict, blank=True, help_text="Trivy scan results")

    container_id = models.CharField(max_length=255, blank=True, null=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Rollback tracking
    is_rollback = models.BooleanField(
        default=False, help_text="Whether this deployment is a rollback")
    rollback_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rollback_deployments',
        help_text="The deployment this was rolled back from")

    class Meta:
        ordering = ['-created_at']

    @property
    def duration_seconds(self):
        """Compute deployment duration in seconds."""
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def __str__(self):
        label = f"{self.service.name} - {self.commit_hash[:7]} ({self.status})"
        if self.is_rollback:
            label = f"[ROLLBACK] {label}"
        return label

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PlatformConfig(models.Model):
    """
    Singleton model for platform-wide domain & SSL configuration.
    Only one row (pk=1) exists. Stores domain, SSL mode, Cloudflare
    API token, and wildcard subdomain settings.
    """
    domain = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Primary domain (e.g. cloud.smsly.cloud)")
    use_ssl = models.BooleanField(
        default=False,
        help_text="Enable HTTPS via Let's Encrypt")
    cloudflare_api_token = EncryptedCharField(
        max_length=255, blank=True, default='',
        help_text="Cloudflare API Token for DNS challenge (Edit zone DNS)")
    wildcard_subdomains = models.BooleanField(
        default=True,
        help_text="Enable wildcard SSL for *.domain deployed services")
    server_ip = models.GenericIPAddressField(
        blank=True, null=True,
        help_text="Server public IP (auto-detected or manual)")
    caddy_status = models.CharField(
        max_length=20, default='unknown',
        help_text="Last known Caddy status")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform Configuration"
        verbose_name_plural = "Platform Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Get or create the singleton config."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        mode = "SSL" if self.use_ssl else "HTTP"
        return f"Platform Config ({self.domain or 'IP-only'} / {mode})"
