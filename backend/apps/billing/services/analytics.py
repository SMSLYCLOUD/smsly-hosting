# pylint: disable=invalid-name
from datetime import timedelta

from apps.billing.models import (
    DailyRevenue,
    InfrastructureCost,
    PricingPlan,
    UserSubscription,
)
from django.db.models import Q, Sum, Count
from django.utils import timezone


class RevenueAnalytics:
    @staticmethod
    def _period_days(period: str) -> int:
        mapping = {
            '7d': 7,
            '30d': 30,
            '90d': 90,
            '180d': 180,
            '1y': 365,
        }
        return mapping.get(str(period or '30d').lower(), 30)

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
        days = self._period_days(period)
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

        cancelled_in_period = UserSubscription.objects.filter(
            status='CANCELLED',
            current_period_end__date__gte=start_date,
        ).count()
        active_count = active_subs.count()
        churn_base = active_count + cancelled_in_period
        churn_rate = ((cancelled_in_period / churn_base) * 100) if churn_base > 0 else 0.0
        avg_revenue_per_user = (mrr / active_count) if active_count > 0 else 0.0
        monthly_churn_fraction = churn_rate / 100
        if monthly_churn_fraction > 0:
            lifetime_value = avg_revenue_per_user / monthly_churn_fraction
        else:
            # No observed churn in period: default to a conservative 24-month window.
            lifetime_value = avg_revenue_per_user * 24

        return {
            'mrr': mrr,
            'arr': arr,
            'total_revenue_period': total_revenue,
            'total_costs_period': total_costs,
            'gross_margin_percent': gross_margin,
            'net_profit_period': total_revenue - total_costs,
            'active_subscribers': active_count,
            'trial_users': UserSubscription.objects.filter(status='TRIAL').count(),
            'churn_rate': churn_rate,
            'avg_revenue_per_user': avg_revenue_per_user,
            'lifetime_value': lifetime_value,
        }

    def get_revenue_chart(self, period='30d', granularity='daily'):
        """Time-series revenue data for charts."""
        days = self._period_days(period)
        start_date = timezone.now().date() - timedelta(days=days)
        data = DailyRevenue.objects.filter(date__gte=start_date).order_by('date')

        if granularity == 'weekly':
            from django.db.models.functions import TruncWeek
            data = data.annotate(period=TruncWeek('date')).values('period').annotate(
                total_revenue=Sum('total_revenue'),
                subscription_revenue=Sum('subscription_revenue'),
                overage_revenue=Sum('overage_revenue'),
            ).order_by('period')
            return [
                {
                    'date': d['period'].isoformat(),
                    'revenue': float(d['total_revenue']),
                    'subscriptions': float(d['subscription_revenue']),
                    'overage': float(d['overage_revenue'])
                } for d in data
            ]
        elif granularity == 'monthly':
            from django.db.models.functions import TruncMonth
            data = data.annotate(period=TruncMonth('date')).values('period').annotate(
                total_revenue=Sum('total_revenue'),
                subscription_revenue=Sum('subscription_revenue'),
                overage_revenue=Sum('overage_revenue'),
            ).order_by('period')
            return [
                {
                    'date': d['period'].isoformat(),
                    'revenue': float(d['total_revenue']),
                    'subscriptions': float(d['subscription_revenue']),
                    'overage': float(d['overage_revenue'])
                } for d in data
            ]

        # daily (default)
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
        # Snapshot of current MRR distribution — single query with plan counts
        plan_ids = list(
            PricingPlan.objects.values_list('id', flat=True)
        )
        plan_sub_counts = {}
        for plan_id, mc, yc in (
            UserSubscription.objects
            .filter(status='ACTIVE', plan_id__in=plan_ids)
            .values('plan_id')
            .annotate(
                monthly_count=Count('id', filter=Q(billing_cycle='MONTHLY')),
                yearly_count=Count('id', filter=Q(billing_cycle='YEARLY')),
            )
            .values_list('plan_id', 'monthly_count', 'yearly_count')
        ):
            plan_sub_counts[plan_id] = (mc, yc)
        plans = PricingPlan.objects.in_bulk(plan_ids)
        data = []
        for plan_id, plan in plans.items():
            monthly_count, yearly_count = plan_sub_counts.get(plan_id, (0, 0))
            plan_mrr = float(plan.price_monthly_usd) * monthly_count + float(plan.price_yearly_usd) / 12 * yearly_count
            if plan_mrr > 0:
                data.append({'name': plan.name, 'value': plan_mrr})
        return data

    def get_top_customers(self, limit=20):
        """Highest-spending customers."""
        # Based on invoices
        # Group by user, sum total
        # This requires aggregation on Invoice
        from django.contrib.auth import get_user_model
        get_user_model()

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
        days = self._period_days(period)
        start_date = timezone.now() - timedelta(days=days)
        cancelled = UserSubscription.objects.filter(
            status='CANCELLED',
            current_period_end__gte=start_date,
        ).count()
        active = UserSubscription.objects.filter(status='ACTIVE').count()
        base = active + cancelled
        churn_rate = ((cancelled / base) * 100) if base > 0 else 0.0
        return {
            'period_days': days,
            'active_subscribers': active,
            'cancelled_subscribers': cancelled,
            'churn_rate_percent': churn_rate,
        }

    def get_infrastructure_costs(self, period='30d'):
        """Cost breakdown by type."""
        days = 30
        start_date = timezone.now().date() - timedelta(days=days)
        costs = InfrastructureCost.objects.filter(date__gte=start_date).values('cost_type').annotate(total=Sum('amount_usd'))
        return [{'name': c['cost_type'], 'value': float(c['total'])} for c in costs]

    def get_profit_forecast(self, months=6):
        months = max(1, int(months or 6))
        lookback_days = 30
        start_date = timezone.now().date() - timedelta(days=lookback_days)

        revenue_total = float(
            DailyRevenue.objects.filter(date__gte=start_date).aggregate(total=Sum('total_revenue'))['total'] or 0
        )
        costs_total = float(
            InfrastructureCost.objects.filter(date__gte=start_date).aggregate(total=Sum('amount_usd'))['total'] or 0
        )
        baseline_revenue = revenue_total
        baseline_cost = costs_total

        forecast = []
        today = timezone.now().date().replace(day=1)
        for idx in range(months):
            month_start = (today + timedelta(days=32 * idx)).replace(day=1)
            projected_revenue = round(baseline_revenue, 2)
            projected_cost = round(baseline_cost, 2)
            forecast.append({
                'month': month_start.isoformat(),
                'projected_revenue': projected_revenue,
                'projected_cost': projected_cost,
                'projected_profit': round(projected_revenue - projected_cost, 2),
            })

        return forecast
