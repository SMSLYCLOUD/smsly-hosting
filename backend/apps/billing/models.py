"""Models module."""
import uuid

from django.conf import settings
from django.db import models

from apps.deployments.models import Service

from .models_analytics import DailyRevenue, InfrastructureCost  # noqa: F401


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


# ─── NEW PRICING & BILLING MODELS ───

class PricingPlan(models.Model):
    """Admin-configurable pricing tiers."""
    name = models.CharField(max_length=100)  # Starter, Pro, Enterprise  # type: ignore[var-annotated]
    slug = models.SlugField(unique=True)  # type: ignore[var-annotated]
    description = models.TextField(blank=True)  # type: ignore[var-annotated]
    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]
    sort_order = models.IntegerField(default=0)  # type: ignore[var-annotated]

    # Resource limits
    max_services = models.IntegerField(default=3)  # type: ignore[var-annotated]
    max_cpu_cores = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)  # type: ignore[var-annotated]
    max_memory_mb = models.IntegerField(default=512)  # type: ignore[var-annotated]
    max_storage_gb = models.IntegerField(default=5)  # type: ignore[var-annotated]
    max_bandwidth_gb = models.IntegerField(default=50)  # type: ignore[var-annotated]
    max_addons = models.IntegerField(default=2)  # type: ignore[var-annotated]
    max_custom_domains = models.IntegerField(default=1)  # type: ignore[var-annotated]
    max_team_members = models.IntegerField(default=1)  # type: ignore[var-annotated]

    # Features
    has_auto_scaling = models.BooleanField(default=False)  # type: ignore[var-annotated]
    has_priority_support = models.BooleanField(default=False)  # type: ignore[var-annotated]
    has_backup = models.BooleanField(default=False)  # type: ignore[var-annotated]
    has_server_transfer = models.BooleanField(default=False)  # type: ignore[var-annotated]
    has_advanced_metrics = models.BooleanField(default=False)  # type: ignore[var-annotated]
    has_ai_diagnosis = models.BooleanField(default=True)  # type: ignore[var-annotated]

    # Pricing
    price_monthly_usd = models.DecimalField(max_digits=10, decimal_places=2)  # type: ignore[var-annotated]
    price_yearly_usd = models.DecimalField(max_digits=10, decimal_places=2)  # annual discount  # type: ignore[var-annotated]

    # Stripe/payment provider IDs
    stripe_price_id_monthly = models.CharField(max_length=100, blank=True)  # type: ignore[var-annotated]
    stripe_price_id_yearly = models.CharField(max_length=100, blank=True)  # type: ignore[var-annotated]
    flutterwave_plan_id = models.CharField(max_length=100, blank=True)  # type: ignore[var-annotated]

    def __str__(self):
        return self.name


class ResourcePrice(models.Model):
    """Per-unit pricing for usage-based billing."""
    resource_type = models.CharField(choices=[  # type: ignore[var-annotated]
        ('CPU', 'CPU Core'), ('RAM', 'RAM (per GB)'),
        ('STORAGE', 'Storage (per GB)'), ('BANDWIDTH', 'Bandwidth (per GB)'),
        ('ADDON_POSTGRES', 'PostgreSQL'), ('ADDON_REDIS', 'Redis'),
        ('ADDON_MONGODB', 'MongoDB'), ('ADDON_QDRANT', 'Qdrant'),
        ('AI_QUERY', 'AI Query'), ('BUILD_MINUTE', 'Build Minute'),
    ], max_length=20, unique=True)
    price_per_unit_monthly = models.DecimalField(max_digits=10, decimal_places=4)  # type: ignore[var-annotated]
    unit_label = models.CharField(max_length=50)  # "per core/month", "per GB/month"  # type: ignore[var-annotated]
    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]

    def __str__(self):
        return f"{self.resource_type}: ${self.price_per_unit_monthly}"


class UserSubscription(models.Model):
    """User's active subscription to a plan."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='active_subscription')  # type: ignore[var-annotated]
    plan = models.ForeignKey(PricingPlan, on_delete=models.PROTECT)  # type: ignore[var-annotated]
    status = models.CharField(choices=[  # type: ignore[var-annotated]
        ('ACTIVE', 'Active'), ('PAST_DUE', 'Past Due'),
        ('CANCELLED', 'Cancelled'), ('TRIAL', 'Trial'),
    ], max_length=20)
    billing_cycle = models.CharField(choices=[  # type: ignore[var-annotated]
        ('MONTHLY', 'Monthly'), ('YEARLY', 'Yearly'),
    ], max_length=10, default='MONTHLY')
    current_period_start = models.DateTimeField()  # type: ignore[var-annotated]
    current_period_end = models.DateTimeField()  # type: ignore[var-annotated]
    stripe_subscription_id = models.CharField(max_length=100, blank=True)  # type: ignore[var-annotated]
    trial_ends_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    def __str__(self):
        return f"{self.user.username} - {self.plan.name}"


class Invoice(models.Model):
    """Generated invoice per billing period."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)  # type: ignore[var-annotated]
    subscription = models.ForeignKey(UserSubscription, on_delete=models.SET_NULL, null=True)  # type: ignore[var-annotated]
    status = models.CharField(choices=[  # type: ignore[var-annotated]
        ('DRAFT', 'Draft'), ('SENT', 'Sent'),
        ('PAID', 'Paid'), ('OVERDUE', 'Overdue'),
    ], max_length=10)
    period_start = models.DateTimeField()  # type: ignore[var-annotated]
    period_end = models.DateTimeField()  # type: ignore[var-annotated]
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)  # type: ignore[var-annotated]
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # type: ignore[var-annotated]
    total = models.DecimalField(max_digits=10, decimal_places=2)  # type: ignore[var-annotated]
    line_items = models.JSONField(default=list)  # [{description, qty, unit_price, total}]  # type: ignore[var-annotated]
    pdf_url = models.URLField(blank=True)  # type: ignore[var-annotated]
    paid_at = models.DateTimeField(null=True)  # type: ignore[var-annotated]
    due_date = models.DateTimeField()  # type: ignore[var-annotated]

    def __str__(self):
        return f"Invoice {self.id} for {self.user.username}"
