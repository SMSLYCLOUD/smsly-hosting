"""Core models for Deployments app."""
import ipaddress
import uuid
import re
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField
from apps.cloud.models import CloudProvider
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField


class ManagedServer(models.Model):
    """
    Represents a remote SMSLY Hosting server that can be controlled
    from this dashboard instance.
    """

    class Status(models.TextChoices):
        ONLINE = "ONLINE", "Online"
        OFFLINE = "OFFLINE", "Offline"
        UNKNOWN = "UNKNOWN", "Unknown"

    class ProvisionStatus(models.TextChoices):
        NONE = "NONE", "Not provisioned"
        PENDING = "PENDING", "Pending"
        PROVISIONING = "PROVISIONING", "Provisioning"
        UPDATING = "UPDATING", "Updating"
        DONE = "DONE", "Done"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="managed_servers",
    )
    project = models.ForeignKey(
        'deployments.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_servers',
        help_text="Project this server belongs to (null = ungrouped)"
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable label, e.g. 'Production VPS' or 'Staging EU'",
    )
    host = models.CharField(
        max_length=255,
        help_text="Public IP address or domain, e.g. '198.51.100.5' or 'prod.example.com'",
    )
    private_ip = models.GenericIPAddressField(
        protocol="IPv4", null=True, blank=True,
        help_text="Internal/Private IP (e.g. AWS Private IP 172.31.x.x)"
    )
    provider_metadata = models.JSONField(
        default=dict, blank=True,
        help_text="Cloud provider metadata (VPC ID, Instance ID, etc.)"
    )

    # ── Connection credentials ──
    api_url = models.URLField(
        blank=True, default="",
        help_text="Full URL to the SMSLY Hosting API",
    )
    api_token = EncryptedCharField(
        max_length=255, blank=True, default="",
    )
    gateway_secret = EncryptedCharField(
        max_length=255, blank=True, default="",
    )

    # ── SSH credentials ──
    ssh_port = models.IntegerField(default=22)
    ssh_user = models.CharField(max_length=100, default="root")
    ssh_password = EncryptedCharField(max_length=255, blank=True, default="")
    ssh_key = EncryptedTextField(blank=True, default="")

    is_primary = models.BooleanField(default=False)
    allow_user_workloads = models.BooleanField(
        default=True,
        help_text="When false, user services cannot be scheduled to this server.",
    )

    # ── Status ──
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    last_health_check = models.DateTimeField(null=True, blank=True)
    server_version = models.CharField(max_length=50, blank=True, default="")
    services_count = models.IntegerField(default=0)

    # ── Cluster Role ──
    class ClusterRole(models.TextChoices):
        LEADER = "LEADER", "Leader"
        FOLLOWER = "FOLLOWER", "Follower"
        CANDIDATE = "CANDIDATE", "Candidate"

    role = models.CharField(
        max_length=20, choices=ClusterRole.choices, default=ClusterRole.FOLLOWER)
    wg_address = models.GenericIPAddressField(
        protocol="IPv4", null=True, blank=True)

    # ── Provisioning ──
    is_lite_agent = models.BooleanField(
        default=False,
        help_text="If true, this server is a lightweight node connecting to the Master's DB/Redis.",
    )
    provision_status = models.CharField(
        max_length=20, choices=ProvisionStatus.choices, default=ProvisionStatus.NONE)
    provision_logs = models.TextField(blank=True, default="")

    @classmethod
    def get_primary(cls):
        """Return the primary/control-plane server."""
        return cls.objects.filter(is_primary=True).first()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "name"]
        verbose_name = "Managed Server"

    def __str__(self):
        return f"{self.name} ({self.host})"


class Project(models.Model):
    """ logical grouping of services. """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    team = models.ForeignKey(
        'teams.Team',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True, default="")
    icon_emoji = models.CharField(max_length=10, default="📦")
    color = models.CharField(max_length=7, default="#6366f1")
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-updated_at"]
        unique_together = ("owner", "slug")
        verbose_name = "Project"

    def __str__(self):
        return f"{self.icon_emoji} {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = re.sub(r'[^a-z0-9]+', '-', self.name.lower()).strip('-')[:100]
            self.slug = base_slug or "project"
            counter = 1
            original_slug = self.slug
            while Project.objects.filter(owner=self.owner, slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


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
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        DELETION_PENDING = 'DELETION_PENDING', 'Deletion Pending'
        DELETION_FAILED = 'DELETION_FAILED', 'Deletion Failed'
        DELETED = 'DELETED', 'Deleted'
        UNKNOWN = 'UNKNOWN', 'Unknown'

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    deletion_error = models.TextField(blank=True, default='')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, blank=True, unique=True)

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

    server = models.ForeignKey(
        'ManagedServer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services_on_node',
        help_text="The managed server where this service is currently hosted"
    )

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
    public_domain_hidden = models.BooleanField(
        default=False,
        help_text="When true, the auto-generated platform domain is not exposed; only custom domains serve traffic.",
    )
    domain_verified = models.BooleanField(default=False)
    verification_token = models.CharField(max_length=64, blank=True)

    # Resource Limits (Simulated for now)
    cpu_cores = models.DecimalField(
        max_digits=6, decimal_places=2, default=1.0)
    memory_mb = models.IntegerField(default=2048)

    # Auto-Scaling
    min_replicas = models.IntegerField(
        default=1, validators=[MinValueValidator(1)])
    max_replicas = models.IntegerField(
        default=3, validators=[MinValueValidator(1)])
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

    # SafeDeploy Config
    safe_deploy_enabled = models.BooleanField(default=False)
    preview_environments_enabled = models.BooleanField(default=False)
    auto_create_preview_on_branch_push = models.BooleanField(default=False)
    MIGRATION_AUTO_APPROVAL_CHOICES = [('NEVER', 'Never'), ('LOW_RISK_ONLY', 'Low Risk Only'), ('LOW_AND_MEDIUM', 'Low and Medium'), ('ALWAYS_REQUIRE_MANUAL', 'Always Require Manual')]
    migration_auto_approval_policy = models.CharField(max_length=50, choices=MIGRATION_AUTO_APPROVAL_CHOICES, default='LOW_RISK_ONLY')
    production_requires_backup = models.BooleanField(default=True)
    health_check_path = models.CharField(max_length=255, default='/health')

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
        default=300, help_text="Seconds to wait for health check response")
    health_check_retries = models.IntegerField(
        default=90, help_text="Consecutive failures before marking unhealthy")
    auto_restart = models.BooleanField(
        default=True, help_text="Automatically restart unhealthy containers")
    health_webhook_token = models.CharField(
        max_length=64, blank=True,
        help_text="Token for the service to push health status to the platform")
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


    # Verified execution metadata (Truthful routing and UI)
    active_target_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="The verified runtime environment where this service is actually executing (e.g., 'local', 'remote', 'lite_agent')."
    )
    active_host_ip = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified IP address or hostname where the service is executing."
    )
    active_runtime_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified container ID or process ID of the running service."
    )
    def __str__(self):
        return f"{self.name} ({self.slug})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = re.sub(r'[^a-z0-9]+', '-', self.name.lower()).strip('-')
            if not self.slug:
                self.slug = str(self.id)[:8]
            
            # Ensure uniqueness
            original_slug = self.slug
            counter = 1
            slug_exists = Service.objects.filter(slug=self.slug)
            if self.pk:
                slug_exists = slug_exists.exclude(pk=self.pk)
            
            while slug_exists.exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
                slug_exists = Service.objects.filter(slug=self.slug)
                if self.pk:
                    slug_exists = slug_exists.exclude(pk=self.pk)

        if not self.verification_token:
            self.verification_token = f"smsly-verify-{uuid.uuid4().hex[:12]}"

        if not getattr(self, 'health_webhook_token', None):
            import secrets
            self.health_webhook_token = secrets.token_urlsafe(32)

        # Use slug for deterministic public domain
        if not self.public_domain:
            import hashlib
            seed = f"{self.owner_id}:{self.slug}".encode()
            short_id = hashlib.sha256(seed).hexdigest()[:6]
            base_domain = self.default_public_base_domain()
            self.public_domain = f"{self.slug}-{short_id}.{base_domain}"

        self.full_clean()
        super().save(*args, **kwargs)

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
        if not self.public_domain:
            return None
        try:
            ipaddress.ip_address(self.public_domain)
            return f"http://{self.public_domain}"
        except ValueError:
            return f"https://{self.public_domain}"

    @classmethod
    def default_public_base_domain(cls) -> str:
        """Resolve the base domain used for generated service subdomains."""
        fallback = "cloud.smsly.cloud"
        configured = (getattr(settings, "DOMAIN", "") or "").strip().lower().rstrip(".")
        if configured in ("localhost", "127.0.0.1"):
            configured = ""

        try:
            from .models_addons import PlatformConfig
            platform_cfg = PlatformConfig.objects.only("domain").first()
            if platform_cfg and platform_cfg.domain:
                configured = platform_cfg.domain.strip().lower().rstrip(".")
        except Exception:
            pass

        # If the resolved domain is a bare IP, it can't be used as a base
        # for service subdomains — wildcard TLS and Let's Encrypt don't
        # support IPs. Fall back to the default cloud domain.
        if configured:
            try:
                ipaddress.ip_address(configured)
                configured = ""
            except ValueError:
                pass

        return configured or fallback


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
    is_locked = models.BooleanField(
        default=False,
        help_text="Locked vars are never overridden by platform auto-injection during deployment")
    SOURCE_CHOICES = [
        ('USER', 'User Defined'),
        ('ADDON', 'Addon Auto-Injected'),
        ('SHORTCODE', 'Shortcode Resolved'),
        ('SYSTEM', 'System Auto-Injected'),
    ]
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES,
        default='USER',
        help_text="Origin of this env var")

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
        BUILD_FAILED = 'BUILD_FAILED', _('Build Failed')
        AWAITING_APPROVAL = 'AWAITING_APPROVAL', _('Awaiting Approval')
        BACKUP_RUNNING = 'BACKUP_RUNNING', _('Backup Running')
        BACKUP_FAILED = 'BACKUP_FAILED', _('Backup Failed')
        MIGRATION_PLANNING = 'MIGRATION_PLANNING', _('Migration Planning')
        MIGRATION_RUNNING = 'MIGRATION_RUNNING', _('Migration Running')
        MIGRATION_FAILED = 'MIGRATION_FAILED', _('Migration Failed')
        DEPLOYING = 'DEPLOYING', _('Deploying')
        HEALTH_CHECK = 'HEALTH_CHECK', _('Health Check')
        TRAFFIC_SHIFTING = 'TRAFFIC_SHIFTING', _('Traffic Shifting')
        MONITORING = 'MONITORING', _('Monitoring')
        STAGED = 'STAGED', _('Staged')
        ACTIVE = 'ACTIVE', _('Active')
        FAILED = 'FAILED', _('Failed')
        CANCELLED = 'CANCELLED', _('Cancelled')
        INACTIVE = 'INACTIVE', _('Inactive')
        ROLLING_BACK = 'ROLLING_BACK', _('Rolling Back')
        ROLLED_BACK = 'ROLLED_BACK', _('Rolled Back')

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
    remote_deployment_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="Deployment ID on a delegated remote server.",
    )

    # Blue-green bake: stores the new (green) container ID while STAGED
    green_container_id = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="Temp container ID during STAGED bake period")

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    staged_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When container entered STAGED status (bake clock start)")

    # Rollback tracking
    is_rollback = models.BooleanField(
        default=False, help_text="Whether this deployment is a rollback")
    source_node = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="Node that triggered this deployment (for multi-deploy)")
    rollback_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rollback_deployments',
        help_text="The deployment this was rolled back from")
    target_server = models.ForeignKey(
        'deployments.ManagedServer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='target_deployments',
        help_text="Server this deployment was explicitly routed to")
    target_is_local = models.BooleanField(
        default=False,
        help_text="True when this deployment was explicitly routed to the local controller")

    class Meta:
        ordering = ['-created_at']

    @property
    def duration_seconds(self) -> float | None:
        """Compute deployment duration in seconds."""
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


    # Post-deployment verification metadata
    verified_target_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="The verified runtime environment where this deployment actually executed."
    )
    verified_host_ip = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified IP address or hostname where the deployment executed."
    )
    verified_runtime_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified container ID or process ID of the deployment."
    )
    verified_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When the execution location was verified."
    )
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
            ).exclude(pk=self.pk).update(status=self.Status.INACTIVE)
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
    max_concurrent_builds = models.PositiveIntegerField(
        default=1,
        help_text="Maximum concurrent builds across the entire node fleet (to prevent OOM)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform Configuration"
        verbose_name_plural = "Platform Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """
        Get or create the singleton config.
        Includes a schema guard to prevent 'relation does not exist' errors
        during initial startup/migration phases.
        """
        import os
        from django.db import connection

        # Schema Guard: Check if the table exists before querying
        table_name = cls._meta.db_table
        if table_name not in connection.introspection.table_names():
            # Return a default instance from ENV without saving to DB
            env_domain = os.environ.get('DOMAIN', '').strip()
            env_ssl = os.environ.get('USE_SSL', '').strip().lower()
            return cls(
                pk=1,
                domain=env_domain,
                use_ssl=env_ssl in ('true', '1', 'yes'),
                cloudflare_api_token=os.environ.get('CLOUDFLARE_API_TOKEN', '').strip(),
                wildcard_subdomains=os.environ.get('WILDCARD_SUBDOMAINS', 'true').lower() in ('true', '1', 'yes'),
                server_ip=os.environ.get('PUBLIC_IP', '').strip(),
            )

        obj, created = cls.objects.get_or_create(pk=1)

        env_domain = os.environ.get('DOMAIN', '').strip()
        env_ssl = os.environ.get('USE_SSL', '').strip().lower()
        env_wildcard = os.environ.get('WILDCARD_SUBDOMAINS', '').strip().lower()
        env_cf_token = os.environ.get('CLOUDFLARE_API_TOKEN', '').strip()
        env_ip = os.environ.get('PUBLIC_IP', '').strip()

        # Never sync a known-fake token from the environment into the DB
        _fake_tokens = {"", "fake", "changeme", "test", "dummy_token_for_testing", "your_cloudflare_api_token"}
        if not env_cf_token or env_cf_token.lower() in _fake_tokens or env_cf_token.startswith("your_"):
            env_cf_token = ""

        changed = False
        if env_domain and not obj.domain:
            obj.domain = env_domain
            changed = True
        if env_ssl in ('true', '1', 'yes') and not obj.use_ssl:
            obj.use_ssl = True
            changed = True
        if env_wildcard in ('true', '1', 'yes') and not obj.wildcard_subdomains:
            obj.wildcard_subdomains = True
            changed = True
        elif created and env_wildcard in ('false', '0', 'no'):
            obj.wildcard_subdomains = False
            changed = True
        if env_cf_token and not obj.cloudflare_api_token:
            obj.cloudflare_api_token = env_cf_token
            changed = True
        # Clear any known-fake token that got persisted
        current_token = (obj.cloudflare_api_token or "").strip()
        if current_token and current_token.lower() in _fake_tokens:
            obj.cloudflare_api_token = ""
            changed = True
        if env_ip and not obj.server_ip:
            obj.server_ip = env_ip
            changed = True

        if changed:
            obj.save()

        # Override the server_ip dynamically on the returned object with environment variable if present,
        # so that each node correctly sees its own public IP as its local server_ip,
        # resolving shared-database conflicts.
        if env_ip:
            obj.server_ip = env_ip

        return obj

    def __str__(self):
        mode = "SSL" if self.use_ssl else "HTTP"
        return f"Platform Config ({self.domain or 'IP-only'} / {mode})"
