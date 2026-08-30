import logging
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import OperationalError, ProgrammingError, models
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField

logger = logging.getLogger(__name__)


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
    ssh_key_passphrase = EncryptedCharField(
        max_length=255, blank=True, default="",
        help_text="Passphrase for the encrypted ssh_key (if any). Encrypted at rest.",
    )

    # ── Node DB credentials (encrypted at rest) ──
    node_db_password = EncryptedCharField(
        max_length=255, blank=True, default="",
        help_text="Dedicated PostgreSQL password for this node's agent. "
                  "Encrypted at rest via FIELD_ENCRYPTION_KEY.",
    )

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

    # ── Node Type ──
    class NodeType(models.TextChoices):
        MASTER = "master", "Master (full stack)"
        NODE = "node", "Node (full stack, no Caddy)"
        AGENT_LITE = "agent-lite", "Agent Lite (minimal)"
        MEDIA = "media", "Media Node (voice + video baremetal)"

    node_type = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=NodeType.choices,
        default=NodeType.AGENT_LITE,
        help_text="Determines provisioning mode and available services.",
    )

    # ── Node Component Selection ──
    # JSON field storing which optional components are enabled on node servers.
    # Keys are component names, values are booleans.  Only meaningful when
    # node_type is "node".  Example:
    #   {"observability": true, "security": true, "crowdsec": false, "falco": false}
    node_components = models.JSONField(  # type: ignore[var-annotated]
        default=dict,
        blank=True,
        help_text=(
            "Optional components enabled on this node. Keys: "
            "observability (cadvisor/node-exporter/docker-labels/promtail), "
            "security (fail2ban/ufw/apparmor/auditd/kernel/gvisor), "
            "crowdsec, falco, spire (spire-agent, spire-agent-ecosystem)."
        ),
    )

    # ── Provisioning ──
    is_lite_agent = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="If true, this server is a lightweight node connecting to the Master's DB/Redis.",
    )
    node_number = models.PositiveIntegerField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="Sequential node number (1, 2, ...). Used for domain naming: grid{N}.domain.",
    )
    node_domain = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True,
        help_text="Computed node domain (e.g. grid1.smsly.cloud). Set during provisioning.",
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

    @property
    def is_media_node(self) -> bool:  # type: ignore[no-untyped-def]
        return self.node_type == self.NodeType.MEDIA

    @classmethod
    def get_primary(cls):
        """Return the primary/control-plane server that is ONLINE."""
        return cls.objects.filter(
            is_primary=True,
            status=cls.Status.ONLINE,
        ).first()

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ["-is_primary", "name"]
        verbose_name = "Managed Server"
        indexes = [
            models.Index(fields=["is_primary", "status"], name="ms_primary_status_idx"),
            models.Index(fields=["owner"], name="ms_owner_idx"),
            models.Index(fields=["status"], name="ms_status_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.host})"


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
    blue_green_auto_promote = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text=(
            "When True, webhook / push-triggered green containers are "
            "auto-promoted to live after BLUE_GREEN_STAGING_HOLD_SECONDS. "
            "When False, greens stay in the staging router until manual "
            "promotion (PaaS landing page still resolves the staging URL)."
        ),
    )
    blue_green_staging_hold_seconds = models.PositiveIntegerField(  # type: ignore[var-annotated]
        default=60,
        help_text=(
            "How long to hold a staged green container before auto-promoting "
            "it. Set to 0 with BLUE_GREEN_AUTO_PROMOTE=0 to require manual "
            "approval for every deploy."
        ),
    )
    default_env_scan_depth = models.CharField(  # type: ignore[var-annotated]
        max_length=20, default='shallow',
        choices=[('shallow', 'Shallow'), ('standard', 'Standard'), ('deep', 'Deep')],
        help_text="Default environment scan depth for new services and ecosystem deploys")
    caddy_ask_secret = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="Caddy on_demand_tls ask shared secret. Set via UI — if empty, "
                  "falls back to CADDY_ASK_SECRET env var. A random ephemeral value "
                  "is generated on each restart if neither is configured.")
    github_app_id = models.CharField(
        max_length=64, blank=True, default='',
        help_text="GitHub App numeric ID. Set via setup_github — "
                  "falls back to GITHUB_APP_ID env var if empty.")
    github_app_private_key = EncryptedCharField(
        max_length=8192, blank=True, default='',
        help_text="GitHub App private key PEM. Set via setup_github — "
                  "falls back to GITHUB_APP_PRIVATE_KEY env var if empty.")
    github_client_id = models.CharField(
        max_length=128, blank=True, default='',
        help_text="GitHub OAuth App Client ID. Falls back to GITHUB_CLIENT_ID env var.")
    github_client_secret = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="GitHub OAuth App Client Secret. Falls back to GITHUB_CLIENT_SECRET env var.")
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

    # ── Auto-Scaling ──────────────────────────────────────────────────
    scale_max_replicas = models.PositiveIntegerField(
        default=5,
        help_text="Maximum number of replica containers allowed per service")
    scale_cpu_high = models.PositiveIntegerField(
        default=80,
        help_text="CPU usage percentage above which a new replica is spawned")
    scale_cooldown_min = models.PositiveIntegerField(
        default=5,
        help_text="Minimum minutes between consecutive scale-up operations")
    node_scorer_min_score = models.PositiveIntegerField(
        default=20,
        help_text="Minimum weighted resource score (0-100) for a node to be accepted for spawning. "
                  "Composite of 40% free mem + 40% free CPU + 20% free disk.")
    node_min_free_ram_pct = models.PositiveIntegerField(
        default=20,
        help_text="Minimum free RAM percentage on a node before refusing to spawn a replica")

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
    grid_allow_control_plane_workloads = models.BooleanField(
        default=True,
        help_text="Allow user workloads on the control-plane / primary node")
    allow_insecure_inter_node_tls = models.BooleanField(
        default=False,
        help_text="Skip TLS verification for inter-node HTTP (dev only)")
    smsly_disable_signature_check = models.BooleanField(
        default=False,
        help_text="Globally disable HMAC signature verification (not recommended)")

    # ── Limits ───────────────────────────────────────────────────────────
    max_upload_size = models.PositiveIntegerField(
        default=104857600,
        help_text="Maximum upload size in bytes (default 100 MB)")
    smsly_max_file_read_size = models.PositiveIntegerField(
        default=10485760,
        help_text="Max file size for container file_read (default 10 MB)")
    caddy_daily_cert_cap = models.PositiveIntegerField(
        default=20,
        help_text="Per-apex daily cap for new TLS certificate issuance")

    # ── Rate Limiting ────────────────────────────────────────────────────
    api_rate_limit = models.PositiveIntegerField(
        default=10000,
        help_text="Per-IP per-minute API rate limit")
    api_rate_limit_fail_closed = models.BooleanField(
        default=False,
        help_text="Fail closed if rate-limit check errors")

    # ── Deploy Pipeline ──────────────────────────────────────────────────
    auto_review_hours = models.PositiveIntegerField(
        default=2,
        help_text="Auto-approve deployments in REVIEW status after this many hours (0 = disabled)")
    auto_promote_hours = models.PositiveIntegerField(
        default=12,
        help_text="Auto-promote deployments in STAGED status after this many hours (0 = disabled)")

    # ── Blue-Green Rollback ─────────────────────────────────────────────
    rollback_grace_minutes = models.PositiveIntegerField(
        default=10,
        help_text="Minutes to preserve rollback backup containers before the stale scanner removes them. "
                  "Set to 0 to disable grace period (rollback containers cleaned immediately after promote).")

    # ── Logging ──────────────────────────────────────────────────────────
    django_log_level = models.CharField(
        max_length=10, default='INFO',
        help_text="Django log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")

    # ── Database HA ────────────────────────────────────────────────────────
    db_ha_enabled = models.BooleanField(
        default=True,
        help_text="Enable PostgreSQL read replica for high availability and read scaling.")

    # ── Traffic Geo ────────────────────────────────────────────────────────
    traffic_geo_enabled = models.BooleanField(
        default=True,
        help_text="Collect Traefik access logs and resolve IP geolocations for the traffic map.")

    # ── Frontend Map Visualization ────────────────────────────────────────
    mapbox_token = EncryptedCharField(
        max_length=512, blank=True, default='',
        help_text="Mapbox GL token for the traffic world map on the Metrics page. "
                  "Falls back to NEXT_PUBLIC_MAPBOX_TOKEN env var if empty. "
                  "Can be left empty to use free OpenFreeMap tiles (no token needed).")

    # ── CrowdSec WAF ──────────────────────────────────────────────────────
    crowdsec_bouncer_key = EncryptedCharField(
        max_length=256, blank=True, default='',
        help_text="CrowdSec bouncer API key for Traefik authentication. "
                  "Falls back to CROWDSEC_BOUNCER_KEY env var if empty.")
    crowdsec_enroll_key = EncryptedCharField(
        max_length=256, blank=True, default='',
        help_text="CrowdSec console enrollment key (optional). "
                  "Falls back to CROWDSEC_ENROLL_KEY env var if empty.")

    # ── SPIFFE mTLS ─────────────────────────────────────────────────────
    mtls_enabled = models.BooleanField(
        default=False,
        help_text="Enable SPIFFE mTLS for platform services. "
                  "Requires platform SPIRE infrastructure to be deployed.")
    mtls_ecosystem_enabled = models.BooleanField(
        default=False,
        help_text="Enable SPIFFE mTLS for user-deployed services. "
                  "Requires ecosystem SPIRE infrastructure to be deployed.")

    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        verbose_name = "Platform Configuration"
        verbose_name_plural = "Platform Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)
        from django.core.cache import cache
        cache.delete(self._CACHE_KEY)

    @classmethod
    def clear_cache(cls):
        """Explicitly clear the cached singleton. Call after bulk updates."""
        from django.core.cache import cache
        cache.delete(cls._CACHE_KEY)

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
        'crowdsec_bouncer_key': ('CROWDSEC_BOUNCER_KEY', ''),
        'crowdsec_enroll_key': ('CROWDSEC_ENROLL_KEY', ''),
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
        except Exception as exc:
            logger.debug("Failed to read PlatformConfig field %s: %s", field, exc)
        return os.environ.get(env_key, env_default or default)

    _CACHE_KEY = 'platform_config_singleton'
    _CACHE_TTL = 300  # 5 minutes

    @classmethod
    def load(cls):
        """
        Get or create the singleton config.
        Includes a schema guard to prevent 'relation does not exist' errors
        during initial startup/migration phases.
        """
        import os

        from django.core.cache import cache
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

        # Return cached config if available
        cached = cache.get(cls._CACHE_KEY)
        if cached is not None:
            # Always override server_ip from env so each node sees its own IP
            env_ip = os.environ.get('PUBLIC_IP', '').strip()
            if env_ip:
                cached.server_ip = env_ip
            return cached

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

        cache.set(cls._CACHE_KEY, obj, cls._CACHE_TTL)
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
