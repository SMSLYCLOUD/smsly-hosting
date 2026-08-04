"""Tasks module."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.db.models import Sum
from django.utils import timezone

from apps.billing.models import Invoice, UsageRecord, UserSubscription
from apps.billing.models.analytics import DailyRevenue, InfrastructureCost
from apps.billing.services.metering import UsageMeter
from apps.deployments.constants import TASK_TIME_LIMIT_DEPLOY, TASK_TIME_LIMIT_MEDIUM, TASK_TIME_LIMIT_STANDARD
from apps.deployments.models import Deployment, Service

logger = logging.getLogger(__name__)

# Pricing: $0.01 per vCPU/hour, $0.005 per GB-RAM/hour
PRICE_CPU_HOUR = Decimal("0.01")
PRICE_RAM_GB_HOUR = Decimal("0.005")


@shared_task(soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
def collect_usage_task() -> None:
    """
    Runs hourly. Snapshots active services and calculates cost.
    """
    try:
        active_services = Service.objects.filter(
            deployments__status=Deployment.Status.ACTIVE
        ).distinct()

        records = []
        for service in active_services:
            cpu = service.cpu_cores
            ram_gb = Decimal(service.memory_mb) / 1024

            cost = (cpu * PRICE_CPU_HOUR) + (ram_gb * PRICE_RAM_GB_HOUR)

            records.append(UsageRecord(
                service=service,
                cpu_cores=cpu,
                memory_mb=service.memory_mb,
                cost=cost,
            ))

        if records:
            UsageRecord.objects.bulk_create(records, batch_size=500)
    except Exception as e:
        logger.error(f"Error collecting usage: {e}")


@shared_task(soft_time_limit=TASK_TIME_LIMIT_DEPLOY[0], time_limit=TASK_TIME_LIMIT_DEPLOY[1])
def generate_monthly_invoices() -> None:
    """Run on 1st of each month — generate invoices for all active subscriptions."""
    meter = UsageMeter()
    # Get subscriptions that need billing (active)
    subscriptions = UserSubscription.objects.filter(status='ACTIVE')

    for sub in subscriptions:
        try:
            now = timezone.now()
            period_end = sub.current_period_end
            period_start = sub.current_period_start

            # Skip if an invoice for this billing period already exists
            # (keeps re-runs and beat catch-ups idempotent).
            if Invoice.objects.filter(subscription=sub, period_start=period_start).exists():
                continue

            # Skip if not yet due
            if period_end > now:
                continue

            # Calculate cost (base plan + usage)
            # For simplicity, calculate_cost returns a total.
            # In a real system, we'd detail line items.
            total_cost = meter.calculate_cost(sub.user, period_start, period_end)

            line_items = [
                {
                    'description': f"Subscription: {sub.plan.name}",
                    'qty': 1,
                    'unit_price': float(total_cost), # Simplified
                    'total': float(total_cost)
                }
            ]

            # Create Invoice
            invoice = Invoice.objects.create(
                user=sub.user,
                subscription=sub,
                status='DRAFT',
                period_start=period_start,
                period_end=period_end,
                subtotal=total_cost,
                tax=0,
                total=total_cost,
                line_items=line_items,
                due_date=now + timedelta(days=7)
            )

            # Advance billing period
            if sub.billing_cycle == 'MONTHLY':
                next_period_end = period_end + timedelta(days=30)
            else:
                next_period_end = period_end + timedelta(days=365)

            sub.current_period_start = period_end
            sub.current_period_end = next_period_end
            sub.save()

            # Mark sent (or integrate with payment provider)
            invoice.status = 'SENT'
            invoice.save()

            logger.info(f"Generated invoice {invoice.id} for user {sub.user.username}")

        except Exception as e:
            logger.error(f"Failed to generate invoice for subscription {sub.id}: {e}")


@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1])
def send_payment_reminders() -> None:
    """Run daily — mark past-due invoices OVERDUE and send reminders."""
    try:
        now = timezone.now()
        # Transition SENT → OVERDUE for invoices past their due_date.
        overdue_count = Invoice.objects.filter(
            status='SENT', due_date__lt=now,
        ).update(status='OVERDUE')
        if overdue_count:
            logger.info("Marked %d invoices OVERDUE.", overdue_count)

        # Send at most one reminder per invoice per day.
        from django.core.cache import cache
        from apps.notifications.tasks import dispatch_notification

        overdue_invoices = Invoice.objects.filter(status='OVERDUE').select_related('user')
        sent = 0
        for invoice in overdue_invoices:
            cache_key = f"billing_reminder:{invoice.id}:{now.strftime('%Y%m%d')}"
            if cache.get(cache_key):
                continue
            dispatch_notification.delay(
                event_type='billing_due',
                user_id=invoice.user_id,
                title='Payment overdue',
                message=(
                    f"Invoice {invoice.id} for {invoice.total} is now overdue. "
                    "Please make a payment as soon as possible."
                ),
                metadata={'invoice_id': str(invoice.id)},
            )
            cache.set(cache_key, 1, 86400)
            sent += 1
        if sent:
            logger.info("Sent %d payment reminders.", sent)
    except Exception as e:
        logger.error("Error sending payment reminders: %s", e)

@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1])
def aggregate_daily_revenue() -> None:
    """Run at midnight — snapshot yesterday's revenue."""
    yesterday = timezone.now().date() - timedelta(days=1)

    if DailyRevenue.objects.filter(date=yesterday).exists():
        return

    # Sum invoices paid yesterday
    total_rev = Invoice.objects.filter(
        paid_at__date=yesterday,
        status='PAID'
    ).aggregate(total=Sum('total'))['total'] or 0

    # Subscriptions created yesterday
    # Note: current_period_start might be updated on renewal, so created_at on subscription would be better
    # But UserSubscription doesn't have created_at in the model I defined (it has current_period_start)
    # I'll rely on Invoice creation for new subs revenue or just use 0 for now.
    new_subs = UserSubscription.objects.filter(
        current_period_start__date=yesterday
    ).count()

    active_subs = UserSubscription.objects.filter(status='ACTIVE').count()

    DailyRevenue.objects.create(
        date=yesterday,
        total_revenue=total_rev,
        subscription_revenue=total_rev,
        overage_revenue=0,
        new_subscriptions=new_subs,
        active_subscribers=active_subs
    )

    from apps.billing.services.alerts import AdminAlertService
    AdminAlertService().check_revenue_drop()

@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1])
def calculate_infrastructure_costs() -> None:
    """Run daily — pull costs from cloud provider APIs."""
    yesterday = timezone.now().date() - timedelta(days=1)

    # Skip if already snapshotted for this date (prevents duplicate rows).
    if InfrastructureCost.objects.filter(date=yesterday, cost_type='VPS').exists():
        return

    # Placeholder: estimate cost based on active resources * cost
    # $5/month per service (approx $0.16/day)
    active_services_count = Service.objects.filter(deployments__status='ACTIVE').distinct().count()
    cost_usd = active_services_count * 0.16

    InfrastructureCost.objects.create(
        date=yesterday,
        cost_type='VPS',
        amount_usd=cost_usd,
        description='Estimated VPS cost'
    )
