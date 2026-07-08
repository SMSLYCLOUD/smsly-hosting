"""Core models for Deployments app."""
import contextlib
import ipaddress
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, ProgrammingError, OperationalError
from django.utils.translation import gettext_lazy as _
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField

from apps.cloud.models import CloudProvider


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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    owner = models.ForeignKey(  # type: ignore[var-annotated]
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="managed_servers",
    )
    project = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_servers',
        help_text="Project this server belongs to (null = ungrouped)"
    )
    name = models.CharField(  # type: ignore[var-annotated]
        max_length=100,
        help_text="Human-readable label, e.g. 'Production VPS' or 'Staging EU'",
    )
    host = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        help_text="Public IP address or domain, e.g. '198.51.100.5' or 'prod.example.com'",
    )
    private_ip = models.GenericIPAddressField(  # type: ignore[var-annotated]
        protocol="IPv4", null=True, blank=True,
        help_text="Internal/Private IP (e.g. AWS Private IP 172.31.x.x)"
    )
    provider_metadata = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="Cloud provider metadata (VPC ID, Instance ID, etc.)"
    )
    hardware_fingerprint = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="Captured hardware identifiers collected during provisioning "
                  "(CPU serial, DMI UUID, MAC addresses, disk serials). Used for "
                  "node identity attestation — verifies the same hardware is connecting.",
    )

    # ── Connection credentials ──
    api_url = models.URLField(  # type: ignore[var-annotated]
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
    ssh_port = models.IntegerField(default=22)  # type: ignore[var-annotated]
    ssh_user = models.CharField(max_length=100, default="root")  # type: ignore[var-annotated]
    ssh_password = EncryptedCharField(max_length=255, blank=True, default="")
    ssh_key = EncryptedTextField(blank=True, default="")

    is_primary = models.BooleanField(default=False)  # type: ignore[var-annotated]
    allow_user_workloads = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text="When false, user services cannot be scheduled to this server.",
    )

    # ── Status ──
    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    last_health_check = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    server_version = models.CharField(max_length=50, blank=True, default="")  # type: ignore[var-annotated]
    services_count = models.IntegerField(default=0)  # type: ignore[var-annotated]

    # ── Cluster Role ──
    class ClusterRole(models.TextChoices):
        LEADER = "LEADER", "Leader"
        FOLLOWER = "FOLLOWER", "Follower"
        CANDIDATE = "CANDIDATE", "Candidate"

    role = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=ClusterRole.choices, default=ClusterRole.FOLLOWER)
    wg_address = models.GenericIPAddressField(  # type: ignore[var-annotated]
        protocol="IPv4", null=True, blank=True)

    # ── Provisioning ──
    is_lite_agent = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="If true, this server is a lightweight node connecting to the Master's DB/Redis.",
    )
    provision_status = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=ProvisionStatus.choices, default=ProvisionStatus.NONE)
    provision_logs = models.TextField(blank=True, default="")  # type: ignore[var-annotated]

    # ── Agent self-registration (lit-agent registrar) ──
    # Distinct from status=ONLINE: ``status`` is the master's outbound
    # health probe; ``agent_ready`` is the agent's own assertion that
    # its installer/registrar has finished bootstrapping end-to-end
    # (containers up, migrations applied, celery worker subscribed,
    # the whole stack is functional). When both are True the node is
    # trusted to accept work.
    agent_ready = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text=(
            "True once the agent's installer/registrar has reported "
            "it has finished bootstrapping and is fully ready to "
            "accept work. Distinct from status=ONLINE which only "
            "indicates the master can reach the API."
        ),
    )
    last_agent_heartbeat_at = models.DateTimeField(  # type: ignore[var-annotated]
        blank=True, null=True,
        help_text=(
            "Last time the agent's registrar sent a heartbeat. Used to "
            "detect silent agent outages even when the API is "
            "unreachable from the master."
        ),
    )
    agent_runtime_info = models.JSONField(  # type: ignore[var-annotated]
        blank=True, default=dict,
        help_text=(
            "Last-seen runtime snapshot from the agent: docker version, "
            "image versions, host uptime, disk/mem. Refreshed on every "
            "heartbeat."
        ),
    )

    # SECURITY: TLS verification controls. ``verify_tls`` defaults to True
    # — the platform refuses to skip certificate verification unless
    # the operator has explicitly opted in (and the env flag
    # `ALLOW_INSECURE_INTER_NODE_TLS` is set, see settings.py).
    # `tls_cert_sha256` is the optional pin: when set, the connection
    # is only accepted if the remote cert's SHA-256 matches.
    verify_tls = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text="If false, the platform skips TLS verification when "
                  "calling this server's API. Requires the "
                  "ALLOW_INSECURE_INTER_NODE_TLS env flag.",
    )
    tls_cert_sha256 = models.CharField(  # type: ignore[var-annotated]
        max_length=64, blank=True, default="",
        help_text="Optional SHA-256 fingerprint of the server's TLS cert "
                  "(hex, no colons). When set, connections are pinned to "
                  "this cert regardless of the system trust store.",
    )

    # ── Registry Access ──
    # Registries this node can authenticate with for image pulls/deployments.
    # Set during provisioning — the installer runs ``docker login`` for each.
    registry_access = models.ManyToManyField(  # type: ignore[var-annotated]
        'deployments.ScopedRegistry',
        blank=True,
        help_text="Registries this node can authenticate with for image pulls/deployments",
    )

    @classmethod
    def get_primary(cls):
        """Return the primary/control-plane server."""
        return cls.objects.filter(is_primary=True).first()

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ["-is_primary", "name"]
        verbose_name = "Managed Server"

    def __str__(self):
        return f"{self.name} ({self.host})"


class Project(models.Model):
    """ logical grouping of services. """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    owner = models.ForeignKey(  # type: ignore[var-annotated]
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    team = models.ForeignKey(  # type: ignore[var-annotated]
        'teams.Team',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    name = models.CharField(max_length=100)  # type: ignore[var-annotated]
    slug = models.SlugField(max_length=120)  # type: ignore[var-annotated]
    description = models.TextField(blank=True, default="")  # type: ignore[var-annotated]
    icon_emoji = models.CharField(max_length=10, default="📦")  # type: ignore[var-annotated]
    color = models.CharField(max_length=7, default="#6366f1")  # type: ignore[var-annotated]
    is_default = models.BooleanField(default=False)  # type: ignore[var-annotated]
    is_ephemeral = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Auto-created for custom-registry deploy; hidden from default project list"
    )

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

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
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        abstract = True


class Region(models.Model):
    """
    Physical deployment regions (e.g. us-east-1, eu-central-1).
    """
    name = models.CharField(max_length=100)  # type: ignore[var-annotated]
    slug = models.SlugField(max_length=100, unique=True)  # type: ignore[var-annotated]
    provider = models.CharField(max_length=50, default='aws')  # type: ignore[var-annotated]
    country_code = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")  # type: ignore[var-annotated]
    city = models.CharField(max_length=100)  # type: ignore[var-annotated]
    lat = models.FloatField(null=True, blank=True)  # type: ignore[var-annotated]
    lng = models.FloatField(null=True, blank=True)  # type: ignore[var-annotated]
    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]

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

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)  # type: ignore[var-annotated]
    deletion_error = models.TextField(blank=True, default='')  # type: ignore[var-annotated]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    name = models.CharField(max_length=255, unique=True)  # type: ignore[var-annotated]
    slug = models.SlugField(max_length=255, blank=True, unique=True)  # type: ignore[var-annotated]

    # Provider Integration
    provider = models.ForeignKey(  # type: ignore[var-annotated]
        CloudProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services')

    # Source Config
    repository_url = models.URLField(  # type: ignore[var-annotated]
        help_text="Git repository URL", blank=True, null=True)
    branch = models.CharField(max_length=255, default='main')  # type: ignore[var-annotated]

    # Deployment Config
    DEPLOY_TYPE_CHOICES = [
        ('GIT', 'Git Repository'),
        ('DOCKER', 'Docker Image'),
        ('UPLOAD', 'File Upload'),
        ('TEMPLATE', 'Predefined Template'),
        ('FUNCTION', 'Serverless Function'),
    ]
    deploy_type = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=DEPLOY_TYPE_CHOICES,
        default='GIT')

    BUILDPACK_CHOICES = [
        ('NIXPACKS', 'Nixpacks'),
        ('DOCKER', 'Dockerfile'),
        ('STATIC', 'Static Site'),
    ]
    buildpack = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=BUILDPACK_CHOICES,
        default='NIXPACKS',
        help_text="Build strategy for source code deployments")

    # Serverless Function Config
    function_code = models.TextField(  # type: ignore[var-annotated]
        blank=True,
        help_text="Raw source code for serverless functions")
    function_runtime = models.CharField(  # type: ignore[var-annotated]
        max_length=50,
        default='nodejs18',
        help_text="Runtime environment (e.g. nodejs18, python3.9)")

    docker_image = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    registry_credential = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.RegistryCredential', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='services',
        help_text='Private registry credentials for image pulls'
    )
    owner = models.ForeignKey(  # type: ignore[var-annotated]
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services')

    server = models.ForeignKey(  # type: ignore[var-annotated]
        'ManagedServer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services_on_node',
        help_text="The managed server where this service is currently hosted"
    )

    # Project grouping (Railway-style)
    project = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='services',
        help_text="Project this service belongs to (null = ungrouped)")

    # Build & Run Config
    build_command = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    start_command = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    root_directory = models.CharField(max_length=255, default='/')  # type: ignore[var-annotated]

    # Network
    internal_port = models.IntegerField(default=8000)  # type: ignore[var-annotated]
    public_domain = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True, unique=True)
    public_domain_hidden = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="When true, the auto-generated platform domain is not exposed; only custom domains serve traffic.",
    )
    domain_verified = models.BooleanField(default=False)  # type: ignore[var-annotated]
    verification_token = models.CharField(max_length=64, blank=True)  # type: ignore[var-annotated]

    # Resource Limits (Simulated for now)
    cpu_cores = models.DecimalField(  # type: ignore[var-annotated]
        max_digits=6, decimal_places=2, default=1.0)
    memory_mb = models.IntegerField(default=2048)  # type: ignore[var-annotated]

    # Auto-Scaling
    min_replicas = models.IntegerField(  # type: ignore[var-annotated]
        default=1, validators=[MinValueValidator(1)])
    max_replicas = models.IntegerField(  # type: ignore[var-annotated]
        default=3, validators=[MinValueValidator(1)])
    autoscale_cpu_target = models.IntegerField(  # type: ignore[var-annotated]
        default=80, help_text="Target CPU utilization percentage (HPA)")
    vpa_enabled = models.BooleanField(  # type: ignore[var-annotated]
        default=False, help_text="Enable Vertical Pod Autoscaling (VPA)")
    alert_config = models.JSONField(  # type: ignore[var-annotated]
        default=dict,
        blank=True,
        help_text="Per-service autoscaler alert thresholds and notification targets.",
    )

    # Security
    disable_crowdsec_waf = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Opt this service out of CrowdSec WAF protection",
    )

    # Multi-Region
    regions = models.ManyToManyField(  # type: ignore[var-annotated]
        Region,
        blank=True,
        related_name='services',
        help_text="Regions to deploy this service to")
    primary_region = models.ForeignKey(  # type: ignore[var-annotated]
        Region,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='primary_services')

    # SafeDeploy Config
    safe_deploy_enabled = models.BooleanField(default=False)  # type: ignore[var-annotated]
    safedeploy_enabled = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="When true, production deploys go through the SafeDeploy pipeline (preview → migration validation → risk classification → manual approval).",
    )
    preview_environments_enabled = models.BooleanField(default=True)  # type: ignore[var-annotated]
    auto_create_preview_on_branch_push = models.BooleanField(default=False)  # type: ignore[var-annotated]
    MIGRATION_AUTO_APPROVAL_CHOICES = [('NEVER', 'Never'), ('LOW_RISK_ONLY', 'Low Risk Only'), ('LOW_AND_MEDIUM', 'Low and Medium'), ('ALWAYS_REQUIRE_MANUAL', 'Always Require Manual')]
    migration_auto_approval_policy = models.CharField(max_length=50, choices=MIGRATION_AUTO_APPROVAL_CHOICES, default='LOW_RISK_ONLY')  # type: ignore[var-annotated]
    production_requires_backup = models.BooleanField(default=True)  # type: ignore[var-annotated]
    health_check_path = models.CharField(max_length=255, default='/health')  # type: ignore[var-annotated]

    # Auto-rollback configuration (per-service opt-out + threshold override).
    auto_rollback_enabled = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text=(
            "Allow this service to be auto-rolled-back when the platform "
            "detects repeated failures or crash loops. Set to False for "
            "sensitive workloads where you want manual control."
        ),
    )
    auto_rollback_threshold = models.PositiveSmallIntegerField(  # type: ignore[var-annotated]
        blank=True,
        null=True,
        help_text=(
            "Optional per-service override for the number of consecutive "
            "failed deployments before auto-rollback fires. Leave blank "
            "to use the platform default (AUTO_ROLLBACK_THRESHOLD setting)."
        ),
    )

    # Deployment Strategy
    DEPLOY_STRATEGY_CHOICES = [
        ('ROLLING', 'Rolling Update'),
        ('BLUE_GREEN', 'Blue/Green'),
        ('CANARY', 'Canary'),
    ]
    deploy_strategy = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=DEPLOY_STRATEGY_CHOICES,
        default='ROLLING',
        help_text="Deployment strategy for this service")
    canary_percentage = models.IntegerField(  # type: ignore[var-annotated]
        default=10,
        help_text="Percentage of traffic routed to canary (1-100)")

    # Legacy compat
    use_blue_green = models.BooleanField(  # type: ignore[var-annotated]
        default=False, help_text="Deprecated: use deploy_strategy instead")

    # Preview Environments
    is_preview = models.BooleanField(default=False)  # type: ignore[var-annotated]
    parent_service = models.ForeignKey(  # type: ignore[var-annotated]
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='previews')
    pr_number = models.IntegerField(null=True, blank=True)  # type: ignore[var-annotated]

    # Coolify Integration
    coolify_uuid = models.CharField(max_length=64, blank=True, null=True, unique=True,  # type: ignore[var-annotated]
                                    help_text="UUID of the application in Coolify")

    # Health Check Configuration
    health_check_path = models.CharField(  # type: ignore[var-annotated]
        max_length=255, default='/health', blank=True,
        help_text="HTTP path for health checks (e.g. /health, /api/health)")
    health_check_port = models.IntegerField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="Port for health checks. Leave blank to auto-detect from PORT env var.")
    health_check_interval = models.IntegerField(  # type: ignore[var-annotated]
        default=30, help_text="Seconds between health checks")
    health_check_timeout = models.IntegerField(  # type: ignore[var-annotated]
        default=300, help_text="Seconds to wait for health check response")
    health_check_retries = models.IntegerField(  # type: ignore[var-annotated]
        default=90, help_text="Consecutive failures before marking unhealthy")
    auto_restart = models.BooleanField(  # type: ignore[var-annotated]
        default=True, help_text="Automatically restart unhealthy containers")
    health_webhook_token = models.CharField(  # type: ignore[var-annotated]
        max_length=64, blank=True,
        help_text="Token for the service to push health status to the platform")
    health_status = models.CharField(  # type: ignore[var-annotated]
        max_length=32, default='unknown',
        choices=[
            ('healthy', 'Healthy'),
            ('unhealthy', 'Unhealthy'),
            ('unknown', 'Unknown'),
            ('starting', 'Starting'),
            ('needs_manual_intervention', 'Needs Manual Intervention'),
        ],
        help_text="Current health status of the service")

    # Restart Policy
    RESTART_POLICY_CHOICES = [
        ('always', 'Always'),
        ('unless-stopped', 'Unless Stopped'),
        ('on-failure', 'On Failure'),
        ('no', 'Never'),
    ]
    restart_policy = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=RESTART_POLICY_CHOICES,
        default='unless-stopped',
        help_text="Docker restart policy for the container")

    # Custom domains
    custom_domains = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text="List of custom domains attached to this service")

    # Deploy Mode (single container vs docker-compose)
    DEPLOY_MODE_CHOICES = [
        ('SINGLE', 'Single Container'),
        ('COMPOSE', 'Docker Compose'),
    ]
    deploy_mode = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=DEPLOY_MODE_CHOICES,
        default='SINGLE',
        help_text="SINGLE = one container, COMPOSE = docker-compose multi-container")
    compose_file = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, default='',
        help_text="Relative path to compose file (e.g. docker-compose.prod.yml)")
    compose_main_service = models.CharField(  # type: ignore[var-annotated]
        max_length=100, blank=True, default='',
        help_text="Name of the primary service in compose for Traefik routing")

    # Domain Visibility
    is_public = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text="If False, Traefik route is disabled; service only reachable via Docker DNS")


    # Verified execution metadata (Truthful routing and UI)
    active_target_type = models.CharField(  # type: ignore[var-annotated]
        max_length=50,
        blank=True,
        null=True,
        help_text="The verified runtime environment where this service is actually executing (e.g., 'local', 'remote', 'lite_agent')."
    )
    active_host_ip = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified IP address or hostname where the service is executing."
    )
    active_runtime_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified container ID or process ID of the running service."
    )

    # Dedicated last-scale timestamp for the autoscaler.
    # Decoupled from `updated_at` so unrelated writes (e.g. health_status
    # updates) do NOT reset the autoscaler cooldown.
    last_scale_at = models.DateTimeField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="Last time the autoscaler scaled this service (used for cooldown).",
    )

    # ── Resource-level ACLs ──
    locked = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Deployment lock — blocks deploys and destructive writes",
    )
    locked_reason = models.TextField(  # type: ignore[var-annotated]
        blank=True, default='',
        help_text="Why this service is locked (shown in UI)",
    )
    restrict_to_creator = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Only the owner can modify this service",
    )
    allowed_actions = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text="Explicitly allowed permission codes — if set, overrides role-based checks",
    )
    restricted_environments = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text="List of allowed deployment target environments (e.g. ['production', 'staging'])",
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
            from .models_addons import PlatformConfig  # type: ignore[attr-defined]  # models_addons re-exports from submodules
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
    service = models.OneToOneField(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='compliance')
    hipaa_compliant = models.BooleanField(default=False)  # type: ignore[var-annotated]
    gdpr_compliant = models.BooleanField(default=False)  # type: ignore[var-annotated]
    soc2_compliant = models.BooleanField(default=False)  # type: ignore[var-annotated]
    data_residency = models.CharField(max_length=50, default='us-east-1')  # type: ignore[var-annotated]

    def __str__(self):
        return f"Compliance for {self.service.name}"


class EnvironmentVariable(TimeStampedModel):
    """
    Environment variables for a service.
    """
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='env_vars')
    key = models.CharField(max_length=255)  # type: ignore[var-annotated]
    value = EncryptedCharField(max_length=10000, blank=True, default='')
    is_secret = models.BooleanField(default=False)  # type: ignore[var-annotated]
    is_locked = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Locked vars are never overridden by platform auto-injection during deployment")
    SOURCE_CHOICES = [
        ('USER', 'User Defined'),
        ('ADDON', 'Addon Auto-Injected'),
        ('SHORTCODE', 'Shortcode Resolved'),
        ('SYSTEM', 'System Auto-Injected'),
    ]
    source = models.CharField(  # type: ignore[var-annotated]
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
        HEALTH_CHECK_FAILED = 'HEALTH_CHECK_FAILED', _('Health Check Failed')
        ACTIVE = 'ACTIVE', _('Active')
        FAILED = 'FAILED', _('Failed')
        CANCELLED = 'CANCELLED', _('Cancelled')
        INACTIVE = 'INACTIVE', _('Inactive')
        ROLLING_BACK = 'ROLLING_BACK', _('Rolling Back')
        ROLLED_BACK = 'ROLLED_BACK', _('Rolled Back')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='deployments')
    commit_hash = models.CharField(max_length=40)  # type: ignore[var-annotated]
    commit_message = models.TextField(blank=True)  # type: ignore[var-annotated]
    branch = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, default='',
        help_text="Branch name this deployment deploys (overrides service default)")

    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )

    build_logs = models.TextField(blank=True)  # type: ignore[var-annotated]
    runtime_logs_url = models.URLField(blank=True, null=True)  # type: ignore[var-annotated]

    pipeline_stages = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text="List of pipeline stages (name, status, duration)")

    ai_diagnosis = models.TextField(  # type: ignore[var-annotated]
        blank=True, help_text="AI suggested fix for failure")

    # Pre-deploy review: stores AI recommendations for user approval
    review_summary = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="AI-recommended resources, env vars, and issues for review")

    # Security
    vulnerability_report = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True, help_text="Trivy scan results")

    container_id = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    remote_deployment_id = models.CharField(  # type: ignore[var-annotated]
        max_length=64,
        blank=True,
        null=True,
        help_text="Deployment ID on a delegated remote server.",
    )

    # Blue-green: stores the new (green) container ID during a deployment
    green_container_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True,
        help_text="Temp container ID for the new version during blue-green swap")

    started_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    finished_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    # Rollback tracking
    is_rollback = models.BooleanField(  # type: ignore[var-annotated]
        default=False, help_text="Whether this deployment is a rollback")
    source_node = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True,
        help_text="Node that triggered this deployment (for multi-deploy)")
    rollback_from = models.ForeignKey(  # type: ignore[var-annotated]
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rollback_deployments',
        help_text="The deployment this was rolled back from")
    target_server = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.ManagedServer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='target_deployments',
        help_text="Server this deployment was explicitly routed to")
    target_is_local = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="True when this deployment was explicitly routed to the local controller")

    ecosystem_retry_count = models.IntegerField(  # type: ignore[var-annotated]
        default=0, db_default=0,
        help_text="Number of times ecosystem deploy has retried this deployment")

    queued_min_replicas = models.IntegerField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="Snapshot of service.min_replicas captured at queue time so the deploy "
                  "executor uses the original replica count even if the autoscaler mutates "
                  "it during the build.")

    metadata = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="Scratch state for in-flight pipeline phases (e.g. pre-migration state for rollback).")

    # Per-deployment registry override: if set, the builder uses this
    # instead of the scoped chain (ScopedRegistry → PlatformConfig).
    registry_override = models.JSONField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="Per-deployment registry override {url, username, password} "
                  "— builder uses this instead of the scoped chain"
    )

    class Meta:
        ordering = ['-created_at']

    @property
    def duration_seconds(self) -> float | None:
        """Compute deployment duration in seconds."""
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


    # Post-deployment verification metadata
    verified_target_type = models.CharField(  # type: ignore[var-annotated]
        max_length=50,
        blank=True,
        null=True,
        help_text="The verified runtime environment where this deployment actually executed."
    )
    verified_host_ip = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified IP address or hostname where the deployment executed."
    )
    verified_runtime_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified container ID or process ID of the deployment."
    )
    verified_at = models.DateTimeField(  # type: ignore[var-annotated]
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
        if self.status == self.Status.ACTIVE and self.service_id:
            from django.db import transaction
            with transaction.atomic():
                locked = Deployment.objects.select_for_update().filter(
                    service_id=self.service_id,
                    status=self.Status.ACTIVE,
                )
                if self.pk:
                    locked = locked.exclude(pk=self.pk)
                if self.remote_deployment_id:
                    with contextlib.suppress(ValueError, TypeError):
                        locked = locked.exclude(pk=uuid.UUID(self.remote_deployment_id))
                locked.update(status=self.Status.INACTIVE)
        super().save(*args, **kwargs)


class PlatformConfig(models.Model):
    """
    Singleton model for platform-wide domain & SSL configuration.
    Only one row (pk=1) exists. Stores domain, SSL mode, Cloudflare
    API token, and wildcard subdomain settings.
    """
    domain = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, default='',
        help_text="Primary domain (e.g. cloud.smsly.cloud)")
    use_ssl = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Enable HTTPS via Let's Encrypt")
    cloudflare_api_token = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="Cloudflare API Token for DNS challenge (Edit zone DNS)")
    wildcard_subdomains = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text="Enable wildcard SSL for *.domain deployed services")
    server_ip = models.GenericIPAddressField(  # type: ignore[var-annotated]
        blank=True, null=True,
        help_text="Server public IP (auto-detected or manual)")
    caddy_status = models.CharField(  # type: ignore[var-annotated]
        max_length=20, default='unknown',
        help_text="Last known Caddy status")
    enable_crowdsec_waf = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Enable CrowdSec WAF to block malicious traffic automatically")
    max_concurrent_builds = models.PositiveIntegerField(  # type: ignore[var-annotated]
        default=1,
        help_text="Maximum concurrent builds across the entire node fleet (to prevent OOM)")
    ecosystem_max_concurrent_builds = models.PositiveIntegerField(  # type: ignore[var-annotated]
        default=2,
        help_text="Maximum concurrent ecosystem builds")
    ecosystem_build_stagger_seconds = models.PositiveIntegerField(  # type: ignore[var-annotated]
        default=30,
        help_text="Seconds between each build start within an ecosystem wave")
    ecosystem_default_wave_size = models.PositiveSmallIntegerField(  # type: ignore[var-annotated]
        default=10,
        help_text="Default number of services per ecosystem deploy wave")
    ecosystem_wave_recheck_seconds = models.PositiveIntegerField(  # type: ignore[var-annotated]
        default=15,
        help_text="Seconds between wave completion rechecks")
    caddy_ask_secret = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="Caddy on_demand_tls ask shared secret. Set via UI — if empty, "
                  "falls back to CADDY_ASK_SECRET env var. A random ephemeral value "
                  "is generated on each restart if neither is configured.")
    github_webhook_secret = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="GitHub webhook secret for push event verification")
    gitlab_webhook_secret = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="GitLab webhook secret for push event verification")
    bitbucket_webhook_secret = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="Bitbucket webhook secret for push event verification")
    recovery_phrase_hash = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="SHA-256 hash of the 12-word recovery phrase (salted). "
                  "Used as last-resort admin account access if all trusted devices are lost.",
    )
    trivy_enabled = models.BooleanField(
        default=True,
        help_text="Enable Trivy container image vulnerability scanning on build")
    trivy_fail_on_severity = models.CharField(
        max_length=16, default='CRITICAL',
        help_text="Minimum severity that blocks the build: LOW, MEDIUM, HIGH, CRITICAL")

    # ── Cosign Image Signing ──────────────────────────────────────────
    cosign_enabled = models.BooleanField(
        default=True,
        help_text="Sign container images with Cosign after build. "
                  "Non-fatal if Cosign is not installed.")
    cosign_require_verification = models.BooleanField(
        default=False,
        help_text="Require Cosign signature verification before deploying images. "
                  "Deployments fail if the image is unsigned or verification fails.")

    # ── Backup Encryption ─────────────────────────────────────────────
    backup_require_encryption = models.BooleanField(
        default=False,
        help_text="Require encryption for server backups. "
                  "Auto-enabled in production (DEBUG=False) via settings.")

    # ── Device Trust (Beta) ────────────────────────────────────────────
    enforce_device_trust = models.BooleanField(
        default=False,
        help_text="[Beta] When enabled, unrecognized devices must register before accessing the platform. "
                  "Requires browser fingerprint collection on the frontend.")

    # ── Billing ──────────────────────────────────────────────────────────
    billing_currency = models.CharField(
        max_length=10, blank=True, default='USD',
        help_text="Billing currency code (e.g. USD, NGN)")
    billing_pro_amount = models.CharField(
        max_length=20, blank=True, default='29.00',
        help_text="Pro plan amount (e.g. 29.00)")
    billing_pro_period_days = models.PositiveIntegerField(
        default=30, help_text="Pro plan billing period in days")

    # ── AI Provider Models ───────────────────────────────────────────────
    # (AI model/key settings are managed via the intelligence.AIProviderSettings model)

    # ── SMSLY Platform Integration ───────────────────────────────────────
    smsly_sms_api_url = models.URLField(
        max_length=300, blank=True, default='http://smsly-sms:8000/api/v1',
        help_text="SMSLY SMS API internal URL")
    smsly_voice_api_url = models.URLField(
        max_length=300, blank=True, default='http://smsly-voice:8000/api/v1',
        help_text="SMSLY Voice API internal URL")
    smsly_platform_api_url = models.URLField(
        max_length=300, blank=True, default='http://smsly-platform-api:8000/api/v1',
        help_text="SMSLY Platform API internal URL")
    smsly_internal_api_key = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="Internal service-to-service API key")

    # ── Alerting ─────────────────────────────────────────────────────────
    alert_phone_number = models.CharField(
        max_length=20, blank=True, default='',
        help_text="Phone number for deployment failure alerts (E.164 format)")
    critical_alert_phone = models.CharField(
        max_length=20, blank=True, default='',
        help_text="Phone number for critical/P0 voice call alerts")
    notify_on_success = models.BooleanField(
        default=False,
        help_text="Enable SMS on successful deployments")

    # ── SMTP / Email ────────────────────────────────────────────────────
    smtp_host = models.CharField(
        max_length=255, blank=True, default='',
        help_text="SMTP server host (e.g. smtp.gmail.com)")
    smtp_port = models.PositiveIntegerField(
        default=587,
        help_text="SMTP server port (default 587 for STARTTLS)")
    smtp_username = models.CharField(
        max_length=255, blank=True, default='',
        help_text="SMTP authentication username")
    smtp_password = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="SMTP authentication password")
    smtp_use_tls = models.BooleanField(
        default=True,
        help_text="Enable STARTTLS encryption")
    smtp_from_email = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Default from address for outgoing emails")
    smtp_from_name = models.CharField(
        max_length=100, blank=True, default='SMSLY',
        help_text="Default from name for outgoing emails")

    # ── Container Registry ───────────────────────────────────────────────
    container_registry_url = models.CharField(
        max_length=255, blank=True, default='registry:5000',
        help_text="Container registry URL (e.g. registry:5000 for internal, or docker.io/ghcr.io for external)")
    registry_user = models.CharField(
        max_length=255, blank=True, default='smsly-registry',
        help_text="Container registry username")
    registry_password = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="Container registry password")

    # ── Observability ────────────────────────────────────────────────────
    sentry_dsn = models.CharField(
        max_length=300, blank=True, default='',
        help_text="Sentry DSN for error tracking")
    sentry_traces_sample_rate = models.FloatField(
        default=0.0, help_text="Sentry traces sample rate (0.0-1.0)")
    sentry_profiles_sample_rate = models.FloatField(
        default=0.0, help_text="Sentry profiles sample rate (0.0-1.0)")
    sentry_environment = models.CharField(
        max_length=50, blank=True, default='production',
        help_text="Sentry environment name")

    # ── Feature Flags ────────────────────────────────────────────────────
    smsly_disable_tier_gates = models.BooleanField(
        default=True,
        help_text="Disable billing tier gates (allow all features)")
    enable_legacy_tunnel_api = models.BooleanField(
        default=False,
        help_text="Enable legacy function-based tunnel API")
    smsly_strict_ssh_host_key_check = models.BooleanField(
        default=False,
        help_text="Strict SSH host-key verification for provisioner")

    # ── Frontend Map Visualization ────────────────────────────────────────
    mapbox_token = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="Mapbox GL token for the traffic world map on the Metrics page. "
                  "Falls back to NEXT_PUBLIC_MAPBOX_TOKEN env var if empty.")

    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        verbose_name = "Platform Configuration"
        verbose_name_plural = "Platform Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)

    CLOUDFLARE_TOKEN_MIN_LENGTH = 40
    CLOUDFLARE_TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_\-]+$')

    def validate_cloudflare_token(self) -> list:
        """
        Validate the Cloudflare API token format.

        Returns a list of human-readable error strings. Empty list means
        the token is acceptable (either empty, which is allowed, or it
        meets the length/charset requirements).
        """
        errors: list[str] = []
        token = (self.cloudflare_api_token or "").strip()
        if not token:
            return errors
        if len(token) < self.CLOUDFLARE_TOKEN_MIN_LENGTH:
            errors.append(
                "Cloudflare API token is too short "
                f"(got {len(token)} chars, minimum {self.CLOUDFLARE_TOKEN_MIN_LENGTH})."
            )
        if not self.CLOUDFLARE_TOKEN_PATTERN.fullmatch(token):
            errors.append(
                "Cloudflare API token contains invalid characters; "
                "only alphanumeric, underscore and dash are allowed."
            )
        return errors

    def clean(self):
        super().clean()
        token_errors = self.validate_cloudflare_token()
        if token_errors:
            raise ValidationError({"cloudflare_api_token": token_errors})

    def get_webhook_secret(self, provider: str) -> str:
        """Return webhook secret for the given provider, falling back to env var."""
        env_key = f'{provider.upper()}_WEBHOOK_SECRET'
        db_val = getattr(self, f'{provider.lower()}_webhook_secret', '') or ''
        if db_val:
            return db_val
        import os
        return os.environ.get(env_key, '')

    # Mapping: (PlatformConfig field name, env var name, default)
    _CONFIG_MAP = {
        'billing_currency': ('BILLING_CURRENCY', 'USD'),
        'billing_pro_amount': ('BILLING_PRO_AMOUNT', '29.00'),
        'billing_pro_period_days': ('BILLING_PRO_PERIOD_DAYS', '30'),
        'container_registry_url': ('CONTAINER_REGISTRY_URL', 'registry:5000'),
        'registry_user': ('REGISTRY_USER', 'smsly-registry'),
        'registry_password': ('REGISTRY_PASSWORD', ''),
        'smsly_sms_api_url': ('SMSLY_SMS_API_URL', 'http://smsly-sms:8000/api/v1'),
        'smsly_voice_api_url': ('SMSLY_VOICE_API_URL', 'http://smsly-voice:8000/api/v1'),
        'smsly_platform_api_url': ('SMSLY_PLATFORM_API_URL', 'http://smsly-platform-api:8000/api/v1'),
        'smsly_internal_api_key': ('SMSLY_INTERNAL_API_KEY', ''),
        'alert_phone_number': ('ALERT_PHONE_NUMBER', ''),
        'critical_alert_phone': ('CRITICAL_ALERT_PHONE', ''),
        'sentry_dsn': ('SENTRY_DSN', ''),
        'sentry_environment': ('SENTRY_ENVIRONMENT', 'production'),
        'sentry_traces_sample_rate': ('SENTRY_TRACES_SAMPLE_RATE', '0.0'),
        'sentry_profiles_sample_rate': ('SENTRY_PROFILES_SAMPLE_RATE', '0.0'),
        'mapbox_token': ('NEXT_PUBLIC_MAPBOX_TOKEN', ''),
    }

    @classmethod
    def get_config_value(cls, field: str, default: str = '') -> str:
        """Return effective config value: PlatformConfig DB first, env var fallback."""
        import os
        mapping = cls._CONFIG_MAP.get(field)
        if not mapping:
            return default
        env_key, env_default = mapping
        try:
            cfg = cls.load()
            db_val = getattr(cfg, field, None)
            if db_val is not None and str(db_val) != '':
                return str(db_val)
        except Exception:
            pass
        return os.environ.get(env_key, env_default or default)

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

        try:
            obj, created = cls.objects.get_or_create(pk=1)
        except (ProgrammingError, OperationalError):
            # Column may not exist yet if a migration is pending.
            # Return a default instance from ENV so the app can start.
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


class TrustedDevice(models.Model):
    """
    A trusted device authorized to administer this platform.

    During first sign-in, the device is enrolled by capturing a hardware
    fingerprint (CPU cores, platform, screen, canvas hash) and storing it
    alongside a cryptographically-random device token. Subsequent sign-ins
    from unrecognized devices require out-of-band verification.

    This prevents credential-stuffing attacks: even with valid credentials,
    an attacker cannot access the platform from an unrecognized device.
    """
    user = models.ForeignKey(  # type: ignore[var-annotated]
        'auth.User', on_delete=models.CASCADE,
        related_name='trusted_devices',
    )
    device_token = models.CharField(  # type: ignore[var-annotated]
        max_length=128, unique=True,
        help_text="Cryptographically random token stored in browser localStorage",
    )
    fingerprint_hash = models.CharField(  # type: ignore[var-annotated]
        max_length=128, db_index=True,
        help_text="SHA-256 hash of combined hardware/software fingerprint signals",
    )
    fingerprint_data = models.JSONField(  # type: ignore[var-annotated]
        blank=True, default=dict,
        help_text="Raw fingerprint signals (canvas hash, WebGL, audio, fonts, CPU, GPU, etc.)",
    )
    trust_method = models.CharField(  # type: ignore[var-annotated]
        max_length=32, default='browser',
        choices=[
            ('browser', 'Browser fingerprint'),
            ('ssh_key', 'SSH public key'),
            ('api_token', 'API token'),
            ('manual', 'Manually approved'),
        ],
        help_text="How this device was enrolled",
    )
    ssh_key_fingerprint = models.CharField(  # type: ignore[var-annotated]
        max_length=128, blank=True, default='',
        help_text="SHA-256 fingerprint of SSH public key (for SSH trust method)",
    )
    label = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, default='',
        help_text="User-assigned label (e.g. 'Work Laptop', 'iPhone')",
    )
    ip_address = models.GenericIPAddressField(  # type: ignore[var-annotated]
        blank=True, null=True,
        help_text="IP address at time of enrollment",
    )
    user_agent = models.TextField(  # type: ignore[var-annotated]
        blank=True, default='',
        help_text="User-Agent string at time of enrollment",
    )
    trust_score = models.IntegerField(  # type: ignore[var-annotated]
        default=0,
        help_text="Aggregated trust score (0-100). Incremented on successful "
                  "interactions, decremented on suspicious activity.",
    )
    trust_score_updated_at = models.DateTimeField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="Last time the trust score was modified.",
    )
    last_seen_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]

    class Meta:
        verbose_name = "Trusted Device"
        verbose_name_plural = "Trusted Devices"
        ordering = ['-last_seen_at']

    def __str__(self):
        return f"{self.label or self.fingerprint_hash[:16]}... ({self.user})"
