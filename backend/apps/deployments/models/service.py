import ipaddress
import logging
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

from apps.cloud.models import CloudProvider

from .core import TimeStampedModel

logger = logging.getLogger(__name__)


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

    # Operator-configured Docker bridge subnet for this project's
    # services. Each service that opts into the internal network gets
    # attached to a per-project bridge built on this CIDR. Empty falls
    # back to PlatformConfig.default_internal_subnet (the platform's
    # shared /24).
    internal_subnet = models.CharField(  # type: ignore[var-annotated]
        max_length=64, blank=True, default="",
        help_text=(
            "Docker bridge subnet (CIDR) for this project's scoped network. "
            "When empty, falls back to PlatformConfig.default_internal_subnet "
            "(default 172.30.224.0/24). Example: 10.99.0.0/24 for an isolated "
            "team bridge."
        ),
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

    def delete(self, *args, **kwargs):
        """Delete all services in this project before deleting the project itself."""
        from .core import Service
        from ..tasks import delete_service_task

        services = Service.objects.filter(project=self)
        for svc in services:
            svc.status = Service.Status.DELETION_PENDING
            svc.save(update_fields=['status'])
            delete_service_task.delay(str(svc.id), force=True)

        return super().delete(*args, **kwargs)


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
    staging_domain = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True,
        help_text="Custom staging domain for webhook deployments (e.g. staging.example.com). "
                  "If blank, auto-generated from service name + base domain.")
    staging_domain_verified = models.BooleanField(default=False)  # type: ignore[var-annotated]

    # Resource Limits (Simulated for now)
    cpu_cores = models.DecimalField(  # type: ignore[var-annotated]
        max_digits=6, decimal_places=2, default=1.0)
    memory_mb = models.IntegerField(default=2048)  # type: ignore[var-annotated]

    # Auto-Scaling
    autoscale_enabled = models.BooleanField(  # type: ignore[var-annotated]
        null=True,
        blank=True,
        help_text="Master toggle for horizontal autoscaling. When disabled, "
                  "the service will not be auto-scaled regardless of other settings. "
                  "NULL/None is treated as enabled by autoscaling query filters.",
    )
    min_replicas = models.IntegerField(  # type: ignore[var-annotated]
        default=1, validators=[MinValueValidator(0)])
    max_replicas = models.IntegerField(  # type: ignore[var-annotated]
        default=3, validators=[MinValueValidator(1)])
    autoscale_cpu_target = models.IntegerField(  # type: ignore[var-annotated]
        default=80, help_text="Target CPU utilization percentage for the autoscaler")
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
    safedeploy_enabled = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="When true, production deploys go through the SafeDeploy pipeline (preview → migration validation → risk classification → manual approval).",
    )
    preview_environments_enabled = models.BooleanField(default=True)  # type: ignore[var-annotated]
    auto_create_preview_on_branch_push = models.BooleanField(default=False)  # type: ignore[var-annotated]
    MIGRATION_AUTO_APPROVAL_CHOICES = [('NEVER', 'Never'), ('LOW_RISK_ONLY', 'Low Risk Only'), ('LOW_AND_MEDIUM', 'Low and Medium'), ('ALWAYS_REQUIRE_MANUAL', 'Always Require Manual')]
    migration_auto_approval_policy = models.CharField(max_length=50, choices=MIGRATION_AUTO_APPROVAL_CHOICES, default='LOW_RISK_ONLY')  # type: ignore[var-annotated]
    production_requires_backup = models.BooleanField(default=True)  # type: ignore[var-annotated]

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

    # DEPRECATED: use deploy_strategy instead. Field is retained for DB schema
    # compatibility only — no application code should read or write it.
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
    # Basic-auth password for this service's PREVIEW hostnames (Caddy
    # basic_auth). Previews often run against cloned production data and
    # must never be publicly browsable. Auto-generated on first preview
    # deploy when left empty; username is always "preview".
    preview_password = models.CharField(  # type: ignore[var-annotated]
        max_length=64, blank=True, default='',
        help_text="Basic-auth password gating this service's preview URLs")

    # GitHub App — monorepo watch paths & bot PR handling
    watch_paths = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text="Glob patterns for monorepo path filtering. "
                  "Empty list = deploy on any file change.",
    )
    BOT_PR_STRATEGY_CHOICES = [
        ('DEPLOY', 'Deploy'),
        ('SKIP', 'Skip'),
        ('COMMENT_ONLY', 'Comment Only'),
    ]
    bot_pr_strategy = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=BOT_PR_STRATEGY_CHOICES,
        default='DEPLOY',
        help_text="How to handle PRs from bots (Dependabot, Renovate, etc.)",
    )
    # Stores the PR comment ID so we can update rather than duplicate comments.
    last_pr_comment_id = models.BigIntegerField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="GitHub comment ID for the most recent PR preview comment",
    )

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

    # Path redirects on this service's own domains
    path_redirects = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text=(
            "Path-to-host redirects served on THIS service's domains. "
            'Each entry is {"path": "/account", "target": "account.example.com"}. '
            "Requests to /account/* are 301-redirected to https://target/... "
            "(prefix stripped, query preserved). Fully user-configurable."
        ))

    # Host aliases that serve this same app (accounts.google.com pattern)
    host_aliases = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text=(
            "Extra hostnames that serve THIS service directly. "
            'Each entry is {"host": "account.example.com", "rewrite_root": "/login"}. '
            "Visiting the alias serves the app; the root path is rewritten to "
            "rewrite_root (e.g. /login) so account.example.com shows the login "
            "page. Other paths pass through unchanged."
        ))

    # URL entry toggles (for node-deployed services)
    wildcard_url_enabled = models.BooleanField(  # type: ignore[var-annotated]
        default=True, null=True, blank=True,
        help_text="Enable the master-proxied wildcard URL (e.g. service.grid.smsly.cloud)")
    node_url_enabled = models.BooleanField(  # type: ignore[var-annotated]
        default=True, null=True, blank=True,
        help_text="Enable the direct node URL (e.g. service.grid-node1.smsly.cloud)")
    wildcard_redirect_custom_domain = models.BooleanField(  # type: ignore[var-annotated]
        default=False, null=True, blank=True,
        help_text=(
            "When enabled, requests to the auto-generated wildcard domain "
            "permanently redirect (301) to this service's first custom domain "
            "instead of proxying."
        ))
    wildcard_internal_only = models.BooleanField(  # type: ignore[var-annotated]
        default=False, null=True, blank=True,
        help_text=(
            "When enabled, the auto-generated wildcard domain is hidden from the "
            "public internet (visitors get the 503 page) but still routes for "
            "internal/mesh traffic. Custom domains keep working normally."
        ))

    # Per-service internal network exposure. When enabled, this
    # service is attached to the project's scoped Docker bridge so
    # other services in the same project can reach it via its container
    # IP on the host (no public DNS, no Cloudflare round-trip). The
    # project-level Project.internal_subnet determines the CIDR.
    use_internal_network = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text=(
            "Attach this service to the project's scoped Docker bridge "
            "for low-latency internal service-to-service traffic. Disable "
            "to keep the service on the shared 'smsly-net' bridge only."
        ),
    )
    # Auto-populated at spawn time: the service's IP on the
    # platform-wide shared bridge ('smsly-platform-net' by default).
    # Other services inside or outside the project can reach this
    # service on this IP without TLS or public DNS. The dual-homing
    # means the service is on both bridges: project-scoped (lowest
    # latency, project members only) and platform-scoped (any
    # internal-network-enabled service, regardless of project).
    platform_internal_ip = models.GenericIPAddressField(  # type: ignore[var-annotated]
        blank=True, null=True,
        help_text=(
            "Auto-populated at spawn time: this service's IP on the "
            "platform-wide shared bridge. Use it for inter-service "
            "traffic that needs to escape the project's scope. Empty "
            "when use_internal_network=False."
        ),
    )

    # ── Service HA Mode ────────────────────────────────────────────────
    # The ServiceHAManager (beat task, every 60s) reads this field to
    # decide the failover strategy. See service_ha.py for the full logic.
    HA_MODE_CHOICES = [
        ('none', 'No HA'),
        ('local', 'Local HA (same-node replicas)'),
        ('remote', 'Remote HA (cross-node failover)'),
    ]
    ha_mode = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=HA_MODE_CHOICES, default='none',
        help_text=(
            "High-availability mode. none = no HA. local = multiple "
            "replicas on the same node (fast failover, survives container "
            "crashes). remote = replica on a different node (survives node "
            "failure — disk, kernel, network, power)."
        ),
    )

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

    # Environment variable scan depth for AI analysis
    SCAN_DEPTH_CHOICES = [
        ('shallow', 'Shallow (.env files only)'),
        ('standard', 'Standard (.env + config files)'),
        ('deep', 'Deep (full codebase scan)'),
    ]
    env_scan_depth = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=SCAN_DEPTH_CHOICES,
        default='shallow',
        help_text="How deeply to scan the repository for environment variables during deployment analysis",
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

    class Meta:
        indexes = [
            models.Index(fields=["status"], name="svc_status_idx"),
            models.Index(fields=["owner", "status"], name="svc_owner_status_idx"),
            models.Index(fields=["project", "status"], name="svc_project_status_idx"),
            models.Index(fields=["server", "status"], name="svc_server_status_idx"),
        ]

    def save(self, *args, **kwargs):
        # ISOLATION INVARIANT: every service belongs to a project. Project
        # membership is what drives the per-project scoped docker network,
        # egress firewall and addon attachment — an orphaned (project=None)
        # service silently escapes all of it. Auto-assign a per-owner
        # "Default" project instead so single-service deploys get the same
        # isolation guarantees as ecosystem deploys.
        if not self.project_id and self.owner_id:
            from django.db import IntegrityError
            try:
                project = Project.objects.get(owner_id=self.owner_id, slug='default')
            except Project.DoesNotExist:
                try:
                    project = Project.objects.create(
                        owner_id=self.owner_id,
                        name='Default',
                        slug='default',
                    )
                except IntegrityError:
                    project = Project.objects.get(owner_id=self.owner_id, slug='default')
            self.project = project

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

        if self.project_id:
            try:
                from .network_scope import ScopedNetwork
                from apps.deployments.services.network_scope import ensure_scoped_network, apply_egress_restrictions
                cfg = ScopedNetwork.resolve_network_config(self.project)
                ensure_scoped_network(cfg)
                apply_egress_restrictions(cfg["name"], cfg.get("allowed_egress_networks", ["0.0.0.0/0"]))
            except Exception as exc:
                logger.debug("Failed to apply scoped network: %s", exc)

    def get_resolved_network_scope(self) -> dict:
        """Resolve effective ScopedNetwork configuration for this service."""
        from .network_scope import ScopedNetwork
        if self.project:
            return ScopedNetwork.resolve_network_config(self.project)
        return {"name": "smsly-net", "driver": "bridge", "isolated": False}

    def get_resolved_registry_scope(self) -> dict:
        """Resolve effective ScopedRegistry credentials for this service."""
        from .registry_scope import ScopedRegistry
        scope_obj = self.project or self.owner
        if scope_obj:
            return ScopedRegistry.resolve_registry_credentials(scope_obj)
        return {}

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
    def safe_deploy_enabled(self) -> bool:  # type: ignore[override]
        """DEPRECATED: backward-compat property mapping old field name → safedeploy_enabled."""
        return self.safedeploy_enabled

    @safe_deploy_enabled.setter
    def safe_deploy_enabled(self, value: bool) -> None:  # type: ignore[override]
        self.safedeploy_enabled = value

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

    @property
    def running_replicas(self) -> int:
        """Count of RUNNING replicas (excludes the primary container)."""
        from apps.autoscaler.models.replica import ServiceReplica
        return ServiceReplica.objects.filter(
            service=self, status='RUNNING'
        ).count()

    def generate_internal_addresses(self) -> list[dict]:
        """Return the container's IPs and the Docker networks it's on.

        Used by the service detail page to surface the IPs that other
        services should connect to. On the scoped ecosystem network
        (smsly-net-a5f086aa, 172.30.224.0/24) these IPs are
        host-internal — no public DNS lookup, no Cloudflare round trip,
        no TLS overhead. Traefik (which is also on the bridge) can
        route to them directly via the Traefik docker provider.

        Returns a list of dicts shaped like::

            [{'network': 'smsly-net-a5f086aa',
              'ip': '172.30.224.5',
              'port': 8080,
              'gateway': '172.30.224.1',
              'aliases': ['smsly-identity-service', 'smsly-identity-service.default.internal']}]

        The first entry is the IP on the project's scoped network if
        the container is on it; that's the recommended value for
        service-to-service env vars.
        """
        try:
            import docker as docker_lib
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            container = None
            try:
                container = client.containers.get(self.name)
            except docker_lib.errors.NotFound:
                candidates = client.containers.list(
                    all=True, filters={'name': self.name}
                )
                for c in candidates:
                    if getattr(c, 'name', '') == self.name:
                        container = c
                        break
                if container is None and candidates:
                    container = candidates[0]
            if container is None:
                return []
            container.reload()
            nets = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
            port = self.internal_port or 8000
            # Project-scoped bridge first (smsly-net-<id>), then the
            # platform-wide bridge (smsly-platform-net by convention),
            # then anything else.
            def _net_sort_key(item):
                name, _ = item
                if name.startswith('smsly-net-') and name != 'smsly-net-a5f086aa':
                    return (0, name)
                if name == 'smsly-platform-net':
                    return (1, name)
                if name == 'smsly-net-a5f086aa':
                    return (2, name)
                if name == 'smsly-net':
                    return (3, name)
                return (4, name)
            out = []
            for net_name, net_data in sorted(nets.items(), key=_net_sort_key):
                ip = net_data.get('IPAddress') or ''
                if not ip:
                    continue
                out.append({
                    'network': net_name,
                    'ip': ip,
                    'port': port,
                    'gateway': net_data.get('Gateway') or '',
                    'aliases': list(net_data.get('Aliases') or []),
                })
            return out
        except Exception:
            return []

    def generate_staging_url(self, commit_hash: str = "") -> str:
        """Generate a staging preview URL for webhook deployments.

        Uses ``staging_domain`` if set, otherwise auto-generates one:
        ``staging-{slug}.{base_domain}`` — persistent per-service so only
        one DNS record is needed.
        """
        import re
        base_domain = self.default_public_base_domain()
        if self.staging_domain:
            return f"https://{self.staging_domain}"
        safe_slug = re.sub(r'[^a-z0-9]+', '-', self.slug.lower()).strip('-')[:30]
        slug = f"staging-{safe_slug}"
        slug = re.sub(r'-+', '-', slug).strip('-')
        return f"https://{slug}.{base_domain}"

    @classmethod
    def default_public_base_domain(cls) -> str:
        """Resolve the base domain used for generated service subdomains."""
        fallback = "cloud.smsly.cloud"
        configured = (getattr(settings, "DOMAIN", "") or "").strip().lower().rstrip(".")
        if configured in ("localhost", "127.0.0.1"):
            configured = ""

        try:
            from .core import PlatformConfig
            platform_cfg = PlatformConfig.objects.only("domain").first()
            if platform_cfg and platform_cfg.domain:
                configured = platform_cfg.domain.strip().lower().rstrip(".")
        except Exception as exc:
            logger.debug("Failed to load PlatformConfig domain: %s", exc)

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
