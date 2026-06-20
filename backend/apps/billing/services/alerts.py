import logging
from datetime import timedelta

from django.utils import timezone

from .models_analytics import DailyRevenue

logger = logging.getLogger(__name__)

class AdminAlertService:
    def check_revenue_drop(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        today = DailyRevenue.objects.filter(date=yesterday).first()
        if not today:
            return

        week_ago = yesterday - timedelta(days=7)
        past_week = DailyRevenue.objects.filter(date__gte=week_ago, date__lt=yesterday)
        if not past_week.exists():
            return

        avg_revenue = sum(d.total_revenue for d in past_week) / past_week.count()
        if avg_revenue > 0 and today.total_revenue < avg_revenue * 0.8:
            logger.warning(f"Revenue drop detected: ${today.total_revenue} vs ${avg_revenue} avg")
            # Trigger notification here

    def check_churn_spike(self):
        # Placeholder for churn monitoring
        pass
