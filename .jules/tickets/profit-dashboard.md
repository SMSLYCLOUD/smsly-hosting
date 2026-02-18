# Admin Profit & Revenue Dashboard

## Context
Platform owner needs full visibility into revenue, costs, margins, and growth. This is the business intelligence layer of SMSLY Hosting — shows real-time P&L, customer metrics, and infrastructure costs.

## Codebase Location
- Admin dashboard: `frontend/src/app/admin-dashboard/`
- Billing models: `backend/apps/billing/models.py`
- Service models: `backend/apps/deployments/models.py`

## Phase 1: Revenue Analytics (Backend)

### 1.1 Analytics models
File: `backend/apps/billing/models_analytics.py` [NEW]

```python
class DailyRevenue(models.Model):
    """Pre-aggregated daily revenue snapshot."""
    date = models.DateField(unique=True)
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2)
    subscription_revenue = models.DecimalField(max_digits=12, decimal_places=2)
    overage_revenue = models.DecimalField(max_digits=12, decimal_places=2)
    new_subscriptions = models.IntegerField(default=0)
    cancellations = models.IntegerField(default=0)
    active_subscribers = models.IntegerField(default=0)
    trial_users = models.IntegerField(default=0)

class InfrastructureCost(models.Model):
    """Track actual infrastructure costs for margin calculation."""
    date = models.DateField()
    cost_type = models.CharField(choices=[
        ('VPS', 'VPS Hosting'), ('BANDWIDTH', 'Bandwidth'),
        ('STORAGE', 'Storage'), ('AI_API', 'AI API Costs'),
        ('DNS', 'DNS/SSL'), ('OTHER', 'Other'),
    ], max_length=20)
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=200, blank=True)
```

### 1.2 Analytics aggregation service
File: `backend/apps/billing/services/analytics.py` [NEW]

```python
class RevenueAnalytics:
    def get_overview(self, period='30d'):
        """Top-level metrics: MRR, ARR, churn, LTV, ARPU."""
        return {
            'mrr': ...,                    # Monthly Recurring Revenue
            'arr': ...,                    # Annual Run Rate
            'total_revenue_period': ...,   # Revenue in period
            'total_costs_period': ...,     # Infrastructure costs
            'gross_margin_percent': ...,   # (revenue - costs) / revenue
            'net_profit_period': ...,      # revenue - costs
            'active_subscribers': ...,
            'trial_users': ...,
            'churn_rate': ...,             # cancellations / start_count
            'avg_revenue_per_user': ...,   # ARPU
            'lifetime_value': ...,         # LTV estimate
        }

    def get_revenue_chart(self, period='30d', granularity='daily'):
        """Time-series revenue data for charts."""

    def get_plan_breakdown(self):
        """Revenue by plan tier."""

    def get_top_customers(self, limit=20):
        """Highest-spending customers."""

    def get_churn_analysis(self, period='90d'):
        """Why customers cancel — correlate with last error/failure."""

    def get_infrastructure_costs(self, period='30d'):
        """Cost breakdown by type."""

    def get_profit_forecast(self, months=6):
        """Simple linear forecast based on growth trend."""
```

### 1.3 Celery tasks for daily aggregation
File: `backend/apps/billing/tasks.py` [MODIFY] — add:

```python
@shared_task
def aggregate_daily_revenue():
    """Run at midnight — snapshot yesterday's revenue."""

@shared_task
def calculate_infrastructure_costs():
    """Run daily — pull costs from cloud provider APIs."""
```

## Phase 2: Admin Dashboard (Frontend)

### 2.1 Revenue overview page
File: `frontend/src/app/admin-dashboard/page.tsx` [MODIFY]

**Top Row — KPI Cards (with trend arrows):**
- MRR (Monthly Recurring Revenue) + % change
- Active Subscribers + growth
- Gross Margin % 
- Net Profit this month

**Revenue Chart:**
- Line/area chart: revenue vs costs over time
- Toggle: daily / weekly / monthly
- Toggle: revenue only / overlay costs / show profit

**Breakdown Cards:**
- Revenue by plan (pie chart)
- Revenue by payment provider (Stripe vs Flutterwave vs Crypto)
- New vs returning revenue

### 2.2 Customer analytics page
File: `frontend/src/app/admin-dashboard/customers/page.tsx` [NEW]

- Total users / active / trial / churned
- Customer table: name, plan, MRR, services count, last deploy, joined date
- Sort by revenue, activity, join date
- Churn risk indicator (inactive >7 days)
- Click through to user detail

### 2.3 Infrastructure costs page
File: `frontend/src/app/admin-dashboard/costs/page.tsx` [NEW]

- Cost breakdown by type (VPS, bandwidth, AI API, storage)
- Cost per customer (total costs / active customers)
- Margin calculator: what if we change pricing?
- AI API cost tracking (per-model usage)

### 2.4 Profit & Loss statement
File: `frontend/src/app/admin-dashboard/pnl/page.tsx` [NEW]

Monthly P&L table:
```
Revenue
  Subscription Revenue    $X,XXX
  Overage Revenue          $XXX
  Total Revenue          $X,XXX

Costs
  VPS Hosting              $XXX
  Bandwidth                 $XX
  AI API Costs              $XX
  Other                     $XX
  Total Costs              $XXX

Gross Profit             $X,XXX
Gross Margin                XX%
```

With month-over-month comparison and year-to-date totals.

## Phase 3: API Endpoints

```
# Admin only
GET /api/v1/admin/analytics/overview/          → KPI metrics
GET /api/v1/admin/analytics/revenue/           → revenue time series
GET /api/v1/admin/analytics/plans/             → revenue by plan
GET /api/v1/admin/analytics/customers/         → customer list + metrics
GET /api/v1/admin/analytics/costs/             → infrastructure costs
GET /api/v1/admin/analytics/pnl/               → profit & loss statement
GET /api/v1/admin/analytics/forecast/          → growth forecast
```

## Phase 4: Automated Alerts for Admin

File: `backend/apps/billing/services/alerts.py` [NEW]

Trigger alerts (email + in-app) when:
- Daily revenue drops >20% vs 7-day average
- Churn rate exceeds 5% monthly
- Infrastructure costs spike >30%
- A customer's payment fails
- Gross margin drops below 50%
- Trial conversion rate drops below 10%

## Validation
1. MRR matches sum of all active subscription prices
2. Revenue chart data matches invoice totals
3. Cost entries can be manually added and appear in P&L
4. Churn rate calculation is correct (verify with known data)
5. Forecast trend line is reasonable
6. Admin permissions — regular users cannot access these endpoints
