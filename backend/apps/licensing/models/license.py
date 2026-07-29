
from django.db import models


class PlatformTier(models.TextChoices):
    COMMUNITY = 'community', 'Community'
    PRO = 'pro', 'Pro'
    ENTERPRISE = 'enterprise', 'Enterprise'

class PlatformLicense(models.Model):
    """
    Singleton model storing the current license state.
    Only one record should exist (use .load() class method).

    Stores:
    - Current license key and tier (Community/Pro/Enterprise)
    - RSA-signed payload for offline validation
    - Feature limits (services, team members)
    """
    license_key = models.TextField(blank=True, default='')  # type: ignore[var-annotated]
    tier = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=PlatformTier.choices,
        default=PlatformTier.COMMUNITY,
    )
    # RSA-signed license data (JSON string)
    license_data = models.TextField(blank=True, default='')  # type: ignore[var-annotated]

    # Cached validation state
    is_valid = models.BooleanField(default=False)  # type: ignore[var-annotated]
    last_validated = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    validation_error = models.TextField(blank=True, default='')  # type: ignore[var-annotated]

    # License metadata (extracted from signed payload)
    licensed_to = models.CharField(max_length=255, blank=True, default='')  # type: ignore[var-annotated]
    instance_id = models.CharField(max_length=64, blank=True, default='')  # type: ignore[var-annotated]
    expires_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    max_services = models.IntegerField(default=3)  # Community default  # type: ignore[var-annotated]
    max_team_members = models.IntegerField(default=1)  # type: ignore[var-annotated]

    # Payment info
    payment_provider = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=[
            ('stripe', 'Stripe'),
            ('paystack', 'Paystack'),
            ('nowpayments', 'NowPayments'),
            ('paypal', 'PayPal'),
            ('manual', 'Manual'),
        ],
        blank=True, default='',
    )
    subscription_id = models.CharField(max_length=255, blank=True, default='')  # type: ignore[var-annotated]

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        verbose_name = 'Platform License'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_community(self):
        return self.tier == PlatformTier.COMMUNITY

    @property
    def is_pro(self):
        return self.tier in (PlatformTier.PRO, PlatformTier.ENTERPRISE)

    @property
    def is_enterprise(self):
        return self.tier == PlatformTier.ENTERPRISE
