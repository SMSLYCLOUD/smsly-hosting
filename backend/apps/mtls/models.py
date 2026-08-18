"""
mTLS Management API
===================
API endpoints for managing SPIFFE mTLS on the smsly-hosting platform.
Generic — works with any tenant service, not SMSLY-specific.
"""

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError


ALLOWED_TRUST_DOMAINS = {"ecosystem.local", "platform.local"}


class MtlsConfig(models.Model):
    """
    Per-service mTLS configuration.

    Each deployed service can have mTLS enabled/disabled independently.
    SPIFFE ID is auto-generated from the app name and trust domain.
    """

    service = models.OneToOneField(
        "deployments.Service",
        on_delete=models.CASCADE,
        related_name="mtls_config",
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Whether mTLS is enabled for this service.",
    )
    trust_domain = models.CharField(
        max_length=255,
        default="platform.local",
        help_text="SPIFFE trust domain for this service.",
    )
    spiffe_id = models.CharField(
        max_length=512,
        blank=True,
        help_text="Auto-generated SPIFFE ID (e.g., spiffe://ecosystem.local/service/my-app).",
    )
    svid_expiry = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the current SVID expires. Updated by the rotation task.",
    )
    last_rotation = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the SVID was last rotated.",
    )
    sidecar_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Enable Envoy sidecar for transparent mTLS. "
            "When enabled, an Envoy proxy is deployed alongside the service "
            "to handle mTLS termination/origination transparently."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "mTLS Configuration"
        verbose_name_plural = "mTLS Configurations"

    def clean(self):
        if self.trust_domain not in ALLOWED_TRUST_DOMAINS:
            raise ValidationError(
                f"Trust domain '{self.trust_domain}' is not allowed. "
                f"User services must use: {', '.join(sorted(ALLOWED_TRUST_DOMAINS))}"
            )

    def save(self, *args, **kwargs):
        self.clean()
        if self.service:
            new_spiffe = f"spiffe://{self.trust_domain}/service/{self.service.name}"
            if self.spiffe_id != new_spiffe:
                self.spiffe_id = new_spiffe
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.service.name} - {'enabled' if self.enabled else 'disabled'}"

    @property
    def is_svid_expired(self) -> bool:
        if not self.enabled:
            return False
        if not self.svid_expiry:
            return True
        return timezone.now() > self.svid_expiry

    @property
    def svid_ttl_remaining(self) -> int:
        if not self.svid_expiry:
            return 0
        delta = self.svid_expiry - timezone.now()
        return max(0, int(delta.total_seconds()))


class MtlsAuthorizationPolicy(models.Model):
    """
    L7 authorization rule: which source service can call which target service.

    Policies are evaluated in priority order (highest first). The first matching
    rule determines the action (ALLOW or DENY). If no rule matches, the default
    is DENY when fail_closed=True.
    """

    class Action(models.TextChoices):
        ALLOW = "allow", "Allow"
        DENY = "deny", "Deny"

    name = models.CharField(
        max_length=255,
        help_text="Human-readable policy name.",
    )
    source_spiffe_id = models.CharField(
        max_length=512,
        help_text=(
            "SPIFFE ID of the caller. Use '*' for any source. "
            "Example: spiffe://ecosystem.local/service/frontend"
        ),
    )
    target_service = models.ForeignKey(
        "deployments.Service",
        on_delete=models.CASCADE,
        related_name="mtls_inbound_policies",
    )
    paths = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Path prefixes this policy applies to. Empty = all paths. '
            'Example: ["/api/", "/internal/"]'
        ),
    )
    methods = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'HTTP methods this policy applies to. Empty = all methods. '
            'Example: ["GET", "POST"]'
        ),
    )
    action = models.CharField(
        max_length=10,
        choices=Action.choices,
        default=Action.ALLOW,
    )
    priority = models.IntegerField(
        default=0,
        help_text="Higher priority rules are evaluated first.",
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Whether this policy is active.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "mTLS Authorization Policy"
        verbose_name_plural = "mTLS Authorization Policies"
        ordering = ["-priority", "id"]

    def __str__(self):
        return f"{self.name}: {self.source_spiffe_id} -> {self.target_service.name} [{self.action}]"

    def matches(self, source_spiffe_id: str, path: str, method: str) -> bool:
        """Check if this policy matches the given request parameters."""
        if not self.enabled:
            return False

        # Check source
        if self.source_spiffe_id != "*":
            if self.source_spiffe_id != source_spiffe_id:
                return False

        # Check paths (empty = all paths)
        if self.paths:
            if not any(path.startswith(p) for p in self.paths):
                return False

        # Check methods (empty = all methods)
        if self.methods:
            if method.upper() not in self.methods:
                return False

        return True
