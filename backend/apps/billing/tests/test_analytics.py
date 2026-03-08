# pylint: disable=invalid-name
"""Tests for billing analytics calculations."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.billing.models import (
    DailyRevenue,
    InfrastructureCost,
    PricingPlan,
    UserSubscription,
)
from apps.billing.services.analytics import RevenueAnalytics


User = get_user_model()


class RevenueAnalyticsTests(TestCase):
    def setUp(self):
        self.plan = PricingPlan.objects.create(
            name="Pro",
            slug="pro",
            price_monthly_usd=29.00,
            price_yearly_usd=290.00,
            max_services=10,
            max_cpu_cores=4.0,
            max_memory_mb=8192,
            max_storage_gb=100,
            max_bandwidth_gb=500,
            max_addons=10,
            max_custom_domains=10,
            max_team_members=10,
        )

        now = timezone.now()
        self.active_user = User.objects.create_user("billing-active", password="pass1234")
        self.cancelled_user = User.objects.create_user("billing-cancelled", password="pass1234")

        UserSubscription.objects.create(
            user=self.active_user,
            plan=self.plan,
            status='ACTIVE',
            billing_cycle='MONTHLY',
            current_period_start=now - timedelta(days=15),
            current_period_end=now + timedelta(days=15),
        )
        UserSubscription.objects.create(
            user=self.cancelled_user,
            plan=self.plan,
            status='CANCELLED',
            billing_cycle='MONTHLY',
            current_period_start=now - timedelta(days=40),
            current_period_end=now - timedelta(days=2),
        )

        DailyRevenue.objects.create(
            date=timezone.now().date(),
            total_revenue=100,
            subscription_revenue=90,
            overage_revenue=10,
        )
        InfrastructureCost.objects.create(
            date=timezone.now().date(),
            cost_type='VPS',
            amount_usd=25,
        )

    def test_overview_calculates_churn_and_ltv(self):
        analytics = RevenueAnalytics()
        overview = analytics.get_overview('30d')
        self.assertIn('churn_rate', overview)
        self.assertGreaterEqual(overview['churn_rate'], 0)
        self.assertIn('lifetime_value', overview)
        self.assertGreaterEqual(overview['lifetime_value'], 0)

    def test_profit_forecast_returns_month_series(self):
        analytics = RevenueAnalytics()
        forecast = analytics.get_profit_forecast(months=3)
        self.assertEqual(len(forecast), 3)
        self.assertIn('projected_profit', forecast[0])
