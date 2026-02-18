# Pricing Configuration & Plan Management

## Context
The platform has a billing app (Stripe, Flutterwave, Cryptomus) and a pricing page, but needs admin-configurable pricing plans, usage-based billing, and automated invoice generation. Admin should be able to set prices for CPU/RAM/storage/bandwidth and create tiered plans without code changes.

## Codebase Location
- Billing app: `backend/apps/billing/`
- Billing models: `backend/apps/billing/models.py`
- Payment providers: `backend/apps/billing/services/stripe.py`, `flutterwave.py`, `cryptomus.py`
- Frontend pricing: `frontend/src/app/pricing/`
- Frontend billing settings: `frontend/src/app/settings/billing/`

## Phase 1: Pricing Plans (Backend)

### 1.1 Plan models
File: `backend/apps/billing/models.py` [MODIFY] — add if not exists:

```python
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

class UserSubscription(models.Model):
    """User's active subscription to a plan."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
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
```

### 1.2 Usage metering service
File: `backend/apps/billing/services/metering.py` [NEW]

```python
class UsageMeter:
    """Tracks resource usage per user per billing period."""

    def record_usage(self, user, resource_type, quantity, timestamp=None):
        """Record a usage data point (called by Celery tasks)."""

    def get_usage_summary(self, user, period_start, period_end):
        """Aggregate usage for billing period."""
        # Returns: {cpu_hours, memory_gb_hours, storage_gb, bandwidth_gb, ...}

    def calculate_cost(self, user, period_start, period_end):
        """Calculate total cost for billing period."""
        # Plan base cost + overage charges

    def check_quota(self, user, resource_type, requested_amount):
        """Check if user can use more of a resource. Returns (allowed, remaining)."""
```

### 1.3 Invoice generation task
File: `backend/apps/billing/tasks.py` [MODIFY] — add:

```python
@shared_task
def generate_monthly_invoices():
    """Run on 1st of each month — generate invoices for all active subscriptions."""

@shared_task
def send_payment_reminders():
    """Run daily — send reminders for overdue invoices."""
```

### 1.4 Quota enforcement middleware
File: `backend/apps/billing/middleware.py` [NEW]

Before creating a service/addon/backup, check user's plan limits:
```python
class QuotaEnforcementMiddleware:
    def check_service_limit(self, user): ...
    def check_addon_limit(self, user): ...
    def check_storage_limit(self, user): ...
```

## Phase 2: Admin Pricing Configuration (Frontend)

### 2.1 Admin pricing dashboard
File: `frontend/src/app/admin-dashboard/pricing/page.tsx` [NEW]

- **Plan editor**: CRUD for pricing plans with drag-to-reorder
- **Resource prices**: table to set per-unit prices
- **Plan comparison preview**: see how plans look on the public pricing page
- **Active subscriptions**: list of users per plan

### 2.2 Public pricing page enhancement
File: `frontend/src/app/pricing/page.tsx` [MODIFY]

- Pull plans dynamically from API (not hardcoded)
- Monthly/yearly toggle with savings badge
- Feature comparison table
- "Start Free Trial" / "Upgrade" CTAs
- FAQ section

### 2.3 User billing dashboard
File: `frontend/src/app/settings/billing/page.tsx` [MODIFY]

- Current plan + usage bar charts
- Upgrade/downgrade buttons
- Invoice history with download PDF
- Payment method management
- Usage breakdown by service

## Phase 3: API Endpoints

```
# Plans (public)
GET    /api/v1/plans/                          → list active plans

# Subscriptions (authenticated)
GET    /api/v1/subscription/                   → current user subscription
POST   /api/v1/subscription/                   → subscribe to plan
PATCH  /api/v1/subscription/                   → change plan
DELETE /api/v1/subscription/                   → cancel

# Invoices
GET    /api/v1/invoices/                       → list user invoices
GET    /api/v1/invoices/{id}/                  → invoice detail
GET    /api/v1/invoices/{id}/pdf/              → download PDF

# Usage
GET    /api/v1/usage/                          → current period usage
GET    /api/v1/usage/history/                  → historical usage

# Admin
POST   /api/v1/admin/plans/                    → create plan
PATCH  /api/v1/admin/plans/{id}/               → update plan
GET    /api/v1/admin/resource-prices/          → list prices
PATCH  /api/v1/admin/resource-prices/{type}/   → update price
```

## Validation
1. Create plan in admin → verify it appears on pricing page
2. User subscribes → verify Stripe checkout works
3. Deploy service beyond plan limit → verify quota enforcement blocks it
4. Generate invoice → verify line items match actual usage
5. Upgrade plan → verify new limits take effect immediately
6. Cancel subscription → verify services stop at period end
