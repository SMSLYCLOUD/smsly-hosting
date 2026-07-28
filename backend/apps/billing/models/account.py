"""Models module."""
import uuid

from django.db import models

from apps.deployments.models import Service


class BillingAccount(models.Model):
    class Plan(models.TextChoices):
        HOBBY = 'HOBBY', 'Hobby'
        PRO = 'PRO', 'Pro'
        ENTERPRISE = 'ENTERPRISE', 'Enterprise'

    class SubscriptionStatus(models.TextChoices):
        NONE = 'NONE', 'None'
        TRIALING = 'TRIALING', 'Trialing'
        ACTIVE = 'ACTIVE', 'Active'
        PAST_DUE = 'PAST_DUE', 'Past due'
        CANCELED = 'CANCELED', 'Canceled'
        UNPAID = 'UNPAID', 'Unpaid'
        INCOMPLETE = 'INCOMPLETE', 'Incomplete'
        INCOMPLETE_EXPIRED = 'INCOMPLETE_EXPIRED', 'Incomplete expired'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    user = models.OneToOneField(  # type: ignore[var-annotated]
        'auth.User',
        on_delete=models.CASCADE,
        related_name='billing_account')
    stripe_customer_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True)
    stripe_subscription_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True)
    plan = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Plan.choices,
        default=Plan.HOBBY,
    )
    subscription_status = models.CharField(  # type: ignore[var-annotated]
        max_length=30,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.NONE,
    )
    current_period_end = models.DateTimeField(blank=True, null=True)  # type: ignore[var-annotated]
    balance = models.DecimalField(  # type: ignore[var-annotated]
        max_digits=10,
        decimal_places=2,
        default=0.00)

    def __str__(self):
        return f"{self.user.username} ({self.stripe_customer_id})"


class BillingPayment(models.Model):
    class Provider(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        FLUTTERWAVE = "flutterwave", "Flutterwave"
        CRYPTOMUS = "cryptomus", "Cryptomus"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Failed"
        CANCELED = "CANCELED", "Canceled"
        EXPIRED = "EXPIRED", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    user = models.ForeignKey(  # type: ignore[var-annotated]
        "auth.User",
        on_delete=models.CASCADE,
        related_name="billing_payments",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)  # type: ignore[var-annotated]
    plan = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=BillingAccount.Plan.choices,
        default=BillingAccount.Plan.HOBBY,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)  # type: ignore[var-annotated]
    currency = models.CharField(max_length=10, default="USD")  # type: ignore[var-annotated]
    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    provider_reference = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    provider_transaction_id = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    checkout_url = models.URLField(blank=True, null=True)  # type: ignore[var-annotated]
    metadata = models.JSONField(default=dict, blank=True)  # type: ignore[var-annotated]
    raw_webhook = models.JSONField(blank=True, null=True)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        indexes = [
            models.Index(fields=["provider", "provider_reference"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_reference} ({self.status})"


class UsageRecord(models.Model):
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='usage_records')
    timestamp = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]

    # Metered Values
    cpu_cores = models.DecimalField(max_digits=4, decimal_places=2)  # type: ignore[var-annotated]
    memory_mb = models.IntegerField()  # type: ignore[var-annotated]
    duration_seconds = models.IntegerField(default=3600)  # Hourly check  # type: ignore[var-annotated]

    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0.0000)  # type: ignore[var-annotated]

    def __str__(self):
        return f"{self.service.name} - {self.timestamp} - ${self.cost}"

    class Meta:
        indexes = [
            models.Index(fields=["service", "-timestamp"], name="billing_usage_svc_ts_idx"),
        ]
