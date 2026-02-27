from django.db import models
import json

class PlatformTier(models.TextChoices):
    COMMUNITY = 'community', 'Community'
    PRO = 'pro', 'Pro'
    ENTERPRISE = 'enterprise', 'Enterprise'

class PlatformLicense(models.Model):
    """
    Singleton model storing the current license state.
    Only one record should exist (use .load() class method).
    """
    license_key = models.TextField(blank=True, default='')
    tier = models.CharField(
        max_length=20,
        choices=PlatformTier.choices,
        default=PlatformTier.COMMUNITY,
    )
    # RSA-signed license data (JSON string)
    license_data = models.TextField(blank=True, default='')

    # Cached validation state
    is_valid = models.BooleanField(default=False)
    last_validated = models.DateTimeField(null=True, blank=True)
    validation_error = models.TextField(blank=True, default='')

    # License metadata (extracted from signed payload)
    licensed_to = models.CharField(max_length=255, blank=True, default='')
    instance_id = models.CharField(max_length=64, blank=True, default='')
    expires_at = models.DateTimeField(null=True, blank=True)
    max_services = models.IntegerField(default=3)  # Community default
    max_team_members = models.IntegerField(default=1)

    # Payment info
    payment_provider = models.CharField(
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
    subscription_id = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
