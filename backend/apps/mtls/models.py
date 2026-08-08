"""
mTLS Management API
===================
API endpoints for managing SPIFFE mTLS on the smsly-hosting platform.
Generic — works with any tenant service, not SMSLY-specific.
"""

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError


ALLOWED_TRUST_DOMAINS = {"ecosystem.local"}


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
        default="ecosystem.local",
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
