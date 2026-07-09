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
            logger.warning("Revenue drop detected: $%s vs $%s avg", today.total_revenue, avg_revenue)
            try:
                from apps.notifications.tasks import dispatch_notification
                from django.contrib.auth import get_user_model
                User = get_user_model()
                admins = User.objects.filter(is_superuser=True)
                for admin in admins:
                    dispatch_notification.delay(
                        event_type='billing_alert',
                        user_id=str(admin.id),
                        title='Revenue Drop Alert',
                        message=f'Revenue dropped to ${today.total_revenue:.2f} (avg: ${avg_revenue:.2f}).',
                    )
            except Exception as exc:
                logger.warning("Failed to dispatch revenue drop alert: %s", exc)

    def check_churn_spike(self):
        # Placeholder for churn monitoring
        pass
