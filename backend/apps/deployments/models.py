"""Models module."""
import uuid
import re
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField
from apps.cloud.models import CloudProvider

# Import AuditLog explicitly to register it with the app
# pylint: disable=unused-import
from .models_audit import AuditLog
from .models_cron import CronJob
from .models_storage import Volume  # Add this
from .api_token_auth import APIToken  # CLI token auth
from .models_servers import ManagedServer  # Multi-server management
from .models_project import Project  # Project grouping
# pylint: enable=unused-import


class TimeStampedModel(models.Model):
    """Abstract base class with created_at and updated_at fields."""
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
        ('FUNCTION', 'Serverless Function'),
    ]
    deploy_type = models.CharField(
        max_length=20,
        choices=DEPLOY_TYPE_CHOICES,
        default='GIT')

    BUILDPACK_CHOICES = [
        ('NIXPACKS', 'Nixpacks'),
        ('DOCKER', 'Dockerfile'),
        ('STATIC', 'Static Site'),
    ]
    buildpack = models.CharField(
        max_length=20,
        choices=BUILDPACK_CHOICES,
        default='NIXPACKS',
        help_text="Build strategy for source code deployments")

    # Serverless Function Config
    function_code = models.TextField(
        blank=True,
        help_text="Raw source code for serverless functions")
    function_runtime = models.CharField(
        max_length=50,
        default='nodejs18',
        help_text="Runtime environment (e.g. nodejs18, python3.9)")

    docker_image = models.CharField(max_length=255, blank=True, null=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services')

    # Project grouping (Railway-style)
    project = models.ForeignKey(
        'deployments.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services',
        help_text="Project this service belongs to (null = ungrouped)")

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

    # Health Check Configuration
    health_check_path = models.CharField(
        max_length=255, default='/health', blank=True,
        help_text="HTTP path for health checks (e.g. /health, /api/health)")
    health_check_port = models.IntegerField(
        null=True, blank=True,
        help_text="Port for health checks. Leave blank to auto-detect from PORT env var.")
    health_check_interval = models.IntegerField(
        default=30, help_text="Seconds between health checks")
    health_check_timeout = models.IntegerField(
        default=5, help_text="Seconds to wait for health check response")
    health_check_retries = models.IntegerField(
        default=3, help_text="Consecutive failures before marking unhealthy")
    auto_restart = models.BooleanField(
        default=True, help_text="Automatically restart unhealthy containers")
    health_status = models.CharField(
        max_length=20, default='unknown',
        choices=[
            ('healthy', 'Healthy'),
            ('unhealthy', 'Unhealthy'),
            ('unknown', 'Unknown'),
            ('starting', 'Starting'),
        ],
        help_text="Current health status of the service")

    # Restart Policy
    RESTART_POLICY_CHOICES = [
        ('always', 'Always'),
        ('unless-stopped', 'Unless Stopped'),
        ('on-failure', 'On Failure'),
        ('no', 'Never'),
    ]
    restart_policy = models.CharField(
        max_length=20, choices=RESTART_POLICY_CHOICES,
        default='unless-stopped',
        help_text="Docker restart policy for the container")

    # Custom domains
    custom_domains = models.JSONField(
        default=list, blank=True,
        help_text="List of custom domains attached to this service")

    # Deploy Mode (single container vs docker-compose)
    DEPLOY_MODE_CHOICES = [
        ('SINGLE', 'Single Container'),
        ('COMPOSE', 'Docker Compose'),
    ]
    deploy_mode = models.CharField(
        max_length=20, choices=DEPLOY_MODE_CHOICES,
        default='SINGLE',
        help_text="SINGLE = one container, COMPOSE = docker-compose multi-container")
    compose_file = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Relative path to compose file (e.g. docker-compose.prod.yml)")
    compose_main_service = models.CharField(
        max_length=100, blank=True, default='',
        help_text="Name of the primary service in compose for Traefik routing")

    # Domain Visibility
    is_public = models.BooleanField(
        default=True,
        help_text="If False, Traefik route is disabled; service only reachable via Docker DNS")

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

    @classmethod
    def default_public_base_domain(cls) -> str:
        """Resolve the base domain used for generated service subdomains."""
        fallback = "cloud.smsly.cloud"
        configured = (getattr(settings, "DOMAIN", "") or "").strip().lower().rstrip(".")
        if configured in ("localhost", "127.0.0.1"):
            configured = ""

        try:
            platform_cfg = PlatformConfig.objects.only("domain").first()
            if platform_cfg and platform_cfg.domain:
                configured = platform_cfg.domain.strip().lower().rstrip(".")
        except Exception:
            # App startup/migrations can run before DB tables exist.
            pass

        return configured or fallback

    def save(self, *args, **kwargs):
        if not self.verification_token:
            self.verification_token = f"smsly-verify-{uuid.uuid4().hex[:12]}"

        # Auto-generate deterministic subdomain from owner + name
        # Same owner + same name = same domain, always.
        if not self.public_domain:
            import hashlib
            slug = re.sub(r'[^a-z0-9]+', '-', self.name.lower()).strip('-')
            slug = (slug[:48]).strip('-') or "service"
            # Deterministic hash: owner_id + service_name → stable short_id
            seed = f"{self.owner_id}:{self.name}".encode()
            short_id = hashlib.sha256(seed).hexdigest()[:6]
            base_domain = self.default_public_base_domain()
            self.public_domain = f"{slug}-{short_id}.{base_domain}"

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
    """
    Environment variables for a service.
    """
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='env_vars')
    key = models.CharField(max_length=255)
    value = EncryptedCharField(max_length=255, blank=True, default='')
    is_secret = models.BooleanField(default=False)

    class Meta:
        unique_together = ('service', 'key')

    def __str__(self):
        return f"{self.key} ({self.service.name})"


class Deployment(TimeStampedModel):
    """
    Represents a single deployment of a service.
    """
    # pylint: disable=too-many-ancestors
    class Status(models.TextChoices):
        """Deployment statuses."""
        QUEUED = 'QUEUED', _('Queued')
        REVIEW = 'REVIEW', _('Review')
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

    pipeline_stages = models.JSONField(
        default=list, blank=True,
        help_text="List of pipeline stages (name, status, duration)")

    ai_diagnosis = models.TextField(
        blank=True, help_text="AI suggested fix for failure")

    # Pre-deploy review: stores AI recommendations for user approval
    review_summary = models.JSONField(
        default=dict, blank=True,
        help_text="AI-recommended resources, env vars, and issues for review")

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
    def duration_seconds(self) -> float | None:
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
        # When a deployment becomes ACTIVE, deactivate all other ACTIVE
        # deployments for the same service (only one can be live at a time).
        if self.status == self.Status.ACTIVE and self.service_id:
            Deployment.objects.filter(
                service_id=self.service_id,
                status=self.Status.ACTIVE,
            ).exclude(pk=self.pk).update(status=self.Status.CANCELLED)
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
        """Get or create the singleton config.

        On first creation or when key fields are empty, auto-populate from
        environment variables so the Settings UI reflects whatever install.sh
        configured.  This runs on every load (not just creation) to handle
        the case where a wipe didn't fully clear the DB row.
        """
        import os
        obj, created = cls.objects.get_or_create(pk=1)

        # Always try to seed empty fields from env vars.
        # This covers: fresh install, partial wipe, and env var changes.
        env_domain = os.environ.get('DOMAIN', '').strip()
        env_ssl = os.environ.get('USE_SSL', '').strip().lower()
        env_wildcard = os.environ.get('WILDCARD_SUBDOMAINS', '').strip().lower()
        env_cf_token = os.environ.get('CLOUDFLARE_API_TOKEN', '').strip()
        env_ip = os.environ.get('PUBLIC_IP', '').strip()

        changed = False

        # Domain: seed if empty
        if env_domain and not obj.domain:
            obj.domain = env_domain
            changed = True

        # SSL: always sync from env if env says true, because the
        # BooleanField default is False and would never trigger
        # "not obj.use_ssl" seeding otherwise.
        if env_ssl in ('true', '1', 'yes') and not obj.use_ssl:
            obj.use_ssl = True
            changed = True

        # Wildcard: sync from env
        if env_wildcard in ('true', '1', 'yes') and not obj.wildcard_subdomains:
            obj.wildcard_subdomains = True
            changed = True
        elif created and env_wildcard in ('false', '0', 'no'):
            obj.wildcard_subdomains = False
            changed = True

        # Cloudflare token: seed if empty
        if env_cf_token and not obj.cloudflare_api_token:
            obj.cloudflare_api_token = env_cf_token
            changed = True

        # Server IP: seed if empty
        if env_ip and not obj.server_ip:
            obj.server_ip = env_ip
            changed = True

        if changed:
            obj.save()
        return obj

    def __str__(self):
        mode = "SSL" if self.use_ssl else "HTTP"
        return f"Platform Config ({self.domain or 'IP-only'} / {mode})"
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .models_transfer import ServerTransfer
