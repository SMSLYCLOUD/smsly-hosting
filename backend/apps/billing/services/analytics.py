# pylint: disable=invalid-name
from django.db.models import Sum, Count
from django.utils import timezone
from datetime import timedelta
from apps.billing.models import DailyRevenue, InfrastructureCost, UserSubscription, Invoice, PricingPlan

class RevenueAnalytics:
    def get_overview(self, period='30d'):
        """Top-level metrics: MRR, ARR, churn, LTV, ARPU."""
        active_subs = UserSubscription.objects.filter(status='ACTIVE').select_related('plan')
        mrr = 0
        for sub in active_subs:
            if sub.billing_cycle == 'MONTHLY':
                mrr += float(sub.plan.price_monthly_usd)
            else:
                mrr += float(sub.plan.price_yearly_usd) / 12

        arr = mrr * 12

        # Calculate revenue/cost from aggregated models
        # Assuming period='30d'
        days = 30
        start_date = timezone.now().date() - timedelta(days=days)

        rev_agg = DailyRevenue.objects.filter(date__gte=start_date).aggregate(
            total=Sum('total_revenue')
        )
        total_revenue = float(rev_agg['total'] or 0)

        cost_agg = InfrastructureCost.objects.filter(date__gte=start_date).aggregate(
            total=Sum('amount_usd')
        )
        total_costs = float(cost_agg['total'] or 0)

        gross_margin = 0
        if total_revenue > 0:
            gross_margin = ((total_revenue - total_costs) / total_revenue) * 100

        return {
            'mrr': mrr,
            'arr': arr,
            'total_revenue_period': total_revenue,
            'total_costs_period': total_costs,
            'gross_margin_percent': gross_margin,
            'net_profit_period': total_revenue - total_costs,
            'active_subscribers': active_subs.count(),
            'trial_users': UserSubscription.objects.filter(status='TRIAL').count(),
            'churn_rate': 0, # TODO: Calculate churn
            'avg_revenue_per_user': mrr / active_subs.count() if active_subs.count() > 0 else 0,
            'lifetime_value': 0, # TODO: LTV
        }

    def get_revenue_chart(self, period='30d', granularity='daily'):
        """Time-series revenue data for charts."""
        days = 30
        start_date = timezone.now().date() - timedelta(days=days)
        data = DailyRevenue.objects.filter(date__gte=start_date).order_by('date')
        return [
            {
                'date': d.date.isoformat(),
                'revenue': float(d.total_revenue),
                'subscriptions': float(d.subscription_revenue),
                'overage': float(d.overage_revenue)
            } for d in data
        ]

    def get_plan_breakdown(self):
        """Revenue by plan tier."""
        # Snapshot of current MRR distribution
        data = []
        plans = PricingPlan.objects.all()
        for plan in plans:
            subs = UserSubscription.objects.filter(status='ACTIVE', plan=plan)
            plan_mrr = 0
            for sub in subs:
                if sub.billing_cycle == 'MONTHLY':
                    plan_mrr += float(plan.price_monthly_usd)
                else:
                    plan_mrr += float(plan.price_yearly_usd) / 12
            if plan_mrr > 0:
                data.append({'name': plan.name, 'value': plan_mrr})
        return data

    def get_top_customers(self, limit=20):
        """Highest-spending customers."""
        # Based on invoices
        # Group by user, sum total
        # This requires aggregation on Invoice
        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Simplified: just return list of active subscriptions sorted by plan price
        subs = UserSubscription.objects.filter(status='ACTIVE').select_related('user', 'plan')
        # Sort manually for now
        sorted_subs = sorted(subs, key=lambda s: s.plan.price_monthly_usd, reverse=True)[:limit]

        return [
            {
                'id': s.user.id,
                'name': s.user.username,
                'plan': s.plan.name,
                'mrr': float(s.plan.price_monthly_usd), # approx
                'joined': s.user.date_joined.isoformat()
            } for s in sorted_subs
        ]

    def get_churn_analysis(self, period='90d'):
        return {}

    def get_infrastructure_costs(self, period='30d'):
        """Cost breakdown by type."""
        days = 30
        start_date = timezone.now().date() - timedelta(days=days)
        costs = InfrastructureCost.objects.filter(date__gte=start_date).values('cost_type').annotate(total=Sum('amount_usd'))
        return [{'name': c['cost_type'], 'value': float(c['total'])} for c in costs]

    def get_profit_forecast(self, months=6):
        return []
