"""Models module."""
import uuid

from django.conf import settings
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'auth.User',
        on_delete=models.CASCADE,
        related_name='billing_account')
    stripe_customer_id = models.CharField(
        max_length=255, blank=True, null=True)
    stripe_subscription_id = models.CharField(
        max_length=255, blank=True, null=True)
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.HOBBY,
    )
    subscription_status = models.CharField(
        max_length=30,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.NONE,
    )
    current_period_end = models.DateTimeField(blank=True, null=True)
    balance = models.DecimalField(
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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="billing_payments",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    plan = models.CharField(
        max_length=20,
        choices=BillingAccount.Plan.choices,
        default=BillingAccount.Plan.HOBBY,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default="USD")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    provider_reference = models.CharField(max_length=255, blank=True, null=True)
    provider_transaction_id = models.CharField(max_length=255, blank=True, null=True)
    checkout_url = models.URLField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    raw_webhook = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["provider", "provider_reference"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.provider}:{self.provider_reference} ({self.status})"


class UsageRecord(models.Model):
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='usage_records')
    timestamp = models.DateTimeField(auto_now_add=True)

    # Metered Values
    cpu_cores = models.DecimalField(max_digits=4, decimal_places=2)
    memory_mb = models.IntegerField()
    duration_seconds = models.IntegerField(default=3600)  # Hourly check

    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0.0000)

    def __str__(self):
        return f"{self.service.name} - {self.timestamp} - ${self.cost}"


# ─── NEW PRICING & BILLING MODELS ───

class PricingPlan(models.Model):
    """Admin-configurable pricing tiers."""
    name = models.CharField(max_length=100)  # Starter, Pro, Enterprise
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    # Resource limits
    max_services = models.IntegerField(default=3)
    max_cpu_cores = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    max_memory_mb = models.IntegerField(default=512)
    max_storage_gb = models.IntegerField(default=5)
    max_bandwidth_gb = models.IntegerField(default=50)
    max_addons = models.IntegerField(default=2)
    max_custom_domains = models.IntegerField(default=1)
    max_team_members = models.IntegerField(default=1)

    # Features
    has_auto_scaling = models.BooleanField(default=False)
    has_priority_support = models.BooleanField(default=False)
    has_backup = models.BooleanField(default=False)
    has_server_transfer = models.BooleanField(default=False)
    has_advanced_metrics = models.BooleanField(default=False)
    has_ai_diagnosis = models.BooleanField(default=True)

    # Pricing
    price_monthly_usd = models.DecimalField(max_digits=10, decimal_places=2)
    price_yearly_usd = models.DecimalField(max_digits=10, decimal_places=2)  # annual discount

    # Stripe/payment provider IDs
    stripe_price_id_monthly = models.CharField(max_length=100, blank=True)
    stripe_price_id_yearly = models.CharField(max_length=100, blank=True)
    flutterwave_plan_id = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.name


class ResourcePrice(models.Model):
    """Per-unit pricing for usage-based billing."""
    resource_type = models.CharField(choices=[
        ('CPU', 'CPU Core'), ('RAM', 'RAM (per GB)'),
        ('STORAGE', 'Storage (per GB)'), ('BANDWIDTH', 'Bandwidth (per GB)'),
        ('ADDON_POSTGRES', 'PostgreSQL'), ('ADDON_REDIS', 'Redis'),
        ('ADDON_MONGODB', 'MongoDB'), ('ADDON_QDRANT', 'Qdrant'),
        ('AI_QUERY', 'AI Query'), ('BUILD_MINUTE', 'Build Minute'),
    ], max_length=20, unique=True)
    price_per_unit_monthly = models.DecimalField(max_digits=10, decimal_places=4)
    unit_label = models.CharField(max_length=50)  # "per core/month", "per GB/month"
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.resource_type}: ${self.price_per_unit_monthly}"


class UserSubscription(models.Model):
    """User's active subscription to a plan."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='active_subscription')
    plan = models.ForeignKey(PricingPlan, on_delete=models.PROTECT)
    status = models.CharField(choices=[
        ('ACTIVE', 'Active'), ('PAST_DUE', 'Past Due'),
        ('CANCELLED', 'Cancelled'), ('TRIAL', 'Trial'),
    ], max_length=20)
    billing_cycle = models.CharField(choices=[
        ('MONTHLY', 'Monthly'), ('YEARLY', 'Yearly'),
    ], max_length=10, default='MONTHLY')
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    stripe_subscription_id = models.CharField(max_length=100, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan.name}"


class Invoice(models.Model):
    """Generated invoice per billing period."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subscription = models.ForeignKey(UserSubscription, on_delete=models.SET_NULL, null=True)
    status = models.CharField(choices=[
        ('DRAFT', 'Draft'), ('SENT', 'Sent'),
        ('PAID', 'Paid'), ('OVERDUE', 'Overdue'),
    ], max_length=10)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    line_items = models.JSONField(default=list)  # [{description, qty, unit_price, total}]
    pdf_url = models.URLField(blank=True)
    paid_at = models.DateTimeField(null=True)
    due_date = models.DateTimeField()

    def __str__(self):
        return f"Invoice {self.id} for {self.user.username}"
