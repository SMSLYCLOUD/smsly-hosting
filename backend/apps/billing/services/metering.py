import logging
from decimal import Decimal

from apps.billing.models import (
    ResourcePrice,
    UsageRecord,
    UserSubscription,
)
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon
from apps.deployments.models.storage import Volume
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

class UsageMeter:
    """Tracks resource usage per user per billing period."""

    def record_usage(self, service, cpu_cores, memory_mb, cost, timestamp=None):
        """Record a usage data point for a service (called by Celery tasks)."""
        from ..models import UsageRecord
        UsageRecord.objects.create(
            service=service,
            cpu_cores=cpu_cores,
            memory_mb=memory_mb,
            cost=cost,
        )

    def get_usage_summary(self, user, period_start, period_end):
        """Aggregate usage for billing period."""
        # Summary dict to return
        summary = {
            'cpu_hours': Decimal('0.0'),
            'memory_gb_hours': Decimal('0.0'),
            'storage_gb': Decimal('0.0'),
            'bandwidth_gb': Decimal('0.0'),
            'active_services': 0,
            'active_addons': 0,
        }

        # 1. Active Services count (snapshot)
        summary['active_services'] = Service.objects.filter(owner=user).count()

        # 2. Active Addons count (snapshot)
        addons_count = Addon.objects.filter(service__owner=user).count()
        summary['active_addons'] = addons_count

        # 3. Storage Usage (snapshot)
        storage = Volume.objects.filter(service__owner=user).aggregate(total=Sum('size_gb'))
        summary['storage_gb'] = Decimal(storage['total'] or 0)

        # 4. Compute Usage from UsageRecord (historical)
        usage_records = UsageRecord.objects.filter(
            service__owner=user,
            timestamp__gte=period_start,
            timestamp__lt=period_end
        )

        # Approximate CPU/RAM hours from records
        cpu_sum = usage_records.aggregate(total=Sum('cpu_cores'))['total'] or 0
        ram_mb_sum = usage_records.aggregate(total=Sum('memory_mb'))['total'] or 0

        # Each record represents 1 hour (as per task schedule)
        summary['cpu_hours'] = Decimal(cpu_sum)
        summary['memory_gb_hours'] = Decimal(ram_mb_sum) / Decimal(1024)

        # 5. Add current unrecorded usage for currently active services
        now = timezone.now()
        active_services = Service.objects.filter(owner=user, deployments__status='ACTIVE').distinct()
        for service in active_services:
            # Find the most recent usage record for this service
            last_record = UsageRecord.objects.filter(service=service).order_by('-timestamp').first()

            # If there's a record, calculate time since then. Otherwise, use service created_at.
            start_time = last_record.timestamp if last_record else service.created_at

            # Ensure start_time is within the current billing period
            start_time = max(start_time, period_start)

            hours_running = Decimal((now - start_time).total_seconds()) / Decimal(3600)
            if hours_running > 0:
                summary['cpu_hours'] += Decimal(service.cpu_cores) * hours_running
                summary['memory_gb_hours'] += (Decimal(service.memory_mb) / Decimal(1024)) * hours_running

        # Round to 2 decimal places
        summary['cpu_hours'] = summary['cpu_hours'].quantize(Decimal('0.01'))
        summary['memory_gb_hours'] = summary['memory_gb_hours'].quantize(Decimal('0.01'))

        return summary

    def calculate_cost(self, user, period_start, period_end):
        """Calculate total cost for billing period including overages."""
        try:
            sub = user.active_subscription
        except UserSubscription.DoesNotExist:
            return Decimal('0.00')

        plan = sub.plan

        # Base cost — yearly subscribers pay the full annual price.
        cost = Decimal(plan.price_monthly_usd if sub.billing_cycle == 'MONTHLY' else plan.price_yearly_usd)

        summary = self.get_usage_summary(user, period_start, period_end)

        # Calculate hours in period for rate limiting context
        # (approx 720 hours/month, but we use actual duration)
        duration_hours = Decimal((period_end - period_start).total_seconds()) / Decimal(3600)
        if duration_hours < 1:
            duration_hours = Decimal(1)

        # 1. CPU Overage
        # Plan limit is "cores", so total allowed core-hours = cores * hours
        allowed_cpu_hours = plan.max_cpu_cores * duration_hours
        cpu_overage = max(Decimal(0), summary['cpu_hours'] - allowed_cpu_hours)
        if cpu_overage > 0:
            price = self._get_price('CPU', Decimal('0.01')) # Fallback $0.01/core-hour
            cost += cpu_overage * price

        # 2. RAM Overage
        # Plan limit is MB, convert to GB
        allowed_ram_gb_hours = (Decimal(plan.max_memory_mb) / Decimal(1024)) * duration_hours
        ram_overage = max(Decimal(0), summary['memory_gb_hours'] - allowed_ram_gb_hours)
        if ram_overage > 0:
            price = self._get_price('RAM', Decimal('0.005')) # Fallback $0.005/GB-hour
            cost += ram_overage * price

        # 3. Storage Overage
        # Storage is a snapshot, not time-based in this simple model (avg usage would be better but snapshot is safer for now)
        allowed_storage = Decimal(plan.max_storage_gb)
        storage_overage = max(Decimal(0), summary['storage_gb'] - allowed_storage)
        if storage_overage > 0:
            price = self._get_price('STORAGE', Decimal('0.10')) # Fallback $0.10/GB/month
            cost += storage_overage * price

        return cost.quantize(Decimal('0.01'))

    def _get_price(self, resource_type, default):
        """Return the unit price for a resource, falling back to *default*.

        NOTE: ResourcePrice.price_per_unit_monthly is a monthly price.
        For CPU and RAM (hourly resources) the caller must divide by 720
        if it uses the DB value.  The *default* values are already hourly
        so they are used directly.
        """
        try:
            rp = ResourcePrice.objects.get(resource_type=resource_type, is_active=True)
            # price_per_unit_monthly is the monthly price. For hourly
            # resources (CPU, RAM) divide by 720 hours/month.
            if resource_type in ('CPU', 'RAM'):
                return rp.price_per_unit_monthly / Decimal(720)
            return rp.price_per_unit_monthly
        except ResourcePrice.DoesNotExist:
            return default

    def check_quota(self, user, resource_type, requested_amount=1):
        """Check if user can use more of a resource. Returns (allowed, remaining)."""
        try:
            sub = user.active_subscription
            plan = sub.plan
        except UserSubscription.DoesNotExist:
            return False, 0

        if resource_type == 'SERVICE':
            current = Service.objects.filter(owner=user).count()
            limit = plan.max_services
            return (current + requested_amount <= limit), limit - current

        elif resource_type == 'CPU':
            current = Service.objects.filter(owner=user).aggregate(total=Sum('cpu_cores'))['total'] or 0
            limit = plan.max_cpu_cores
            return (current + requested_amount <= limit), limit - current

        elif resource_type == 'MEMORY':
            current = Service.objects.filter(owner=user).aggregate(total=Sum('memory_mb'))['total'] or 0
            limit = plan.max_memory_mb
            return (current + requested_amount <= limit), limit - current

        elif resource_type == 'STORAGE':
            current = Volume.objects.filter(service__owner=user).aggregate(total=Sum('size_gb'))['total'] or 0
            limit = plan.max_storage_gb
            return (current + requested_amount <= limit), limit - current

        elif resource_type == 'ADDON':
            current = Addon.objects.filter(service__owner=user).count()
            limit = plan.max_addons
            return (current + requested_amount <= limit), limit - current

        return True, 9999
