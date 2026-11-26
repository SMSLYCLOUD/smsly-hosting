from __future__ import annotations

import logging
from decimal import Decimal

from apps.billing.models import (
    BillingAccount,
    ResourcePrice,
    UsageRecord,
    UserSubscription,
)
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon
from apps.deployments.models.storage import Volume
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

# Fallback hourly/monthly unit rates used only when ResourcePrice has not been seeded.
DEFAULT_RESOURCE_PRICES: dict[str, Decimal] = {
    'CPU': Decimal('0.01'),       # per core-hour
    'RAM': Decimal('0.005'),      # per GB-hour
    'STORAGE': Decimal('0.10'),   # per GB-month
}

class UsageMeter:
    """Tracks resource usage per user per billing period."""

    def record_usage(self, service: Service, cpu_cores: Decimal, memory_mb: int, cost: Decimal, timestamp: object = None) -> None:
        """Record a usage data point for a service (called by Celery tasks)."""
        from ..models import UsageRecord
        UsageRecord.objects.create(
            service=service,
            cpu_cores=cpu_cores,
            memory_mb=memory_mb,
            cost=cost,
        )

    def get_usage_summary(self, user: object, period_start: object, period_end: object) -> dict[str, Decimal]:
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

        deleted_states = [Service.Status.DELETED, Service.Status.DELETION_PENDING]

        # 1. Active Services count (snapshot)
        summary['active_services'] = Service.objects.filter(owner=user).exclude(
            status__in=deleted_states
        ).count()

        # 2. Active Addons count (snapshot)
        addons_count = Addon.objects.filter(service__owner=user).exclude(
            service__status__in=deleted_states
        ).exclude(
            status__in=[Addon.Status.DELETED, Addon.Status.DELETION_PENDING]
        ).count()
        summary['active_addons'] = addons_count

        # 3. Storage Usage (snapshot)
        storage = Volume.objects.filter(service__owner=user).exclude(
            service__status__in=deleted_states
        ).aggregate(total=Sum('size_gb'))
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
        active_services = Service.objects.filter(
            owner=user, deployments__status='ACTIVE'
        ).exclude(
            status__in=deleted_states
        ).distinct()
        # Bulk-fetch the most recent usage record per service to avoid N+1 queries
        from django.db.models import Subquery, OuterRef
        latest_record_timestamps = UsageRecord.objects.filter(
            service=OuterRef('pk'),
        ).order_by('-timestamp').values('timestamp')[:1]
        active_services = active_services.annotate(
            last_record_ts=Subquery(latest_record_timestamps)
        )
        for service in active_services:
            # If there's a record, calculate time since then. Otherwise, use service created_at.
            start_time = service.last_record_ts if service.last_record_ts else service.created_at

            # Ensure start_time is within the current billing period
            start_time = max(start_time, period_start)

            hours_running = Decimal((now - start_time).total_seconds()) / Decimal(3600)
            if hours_running > 0:
                replicas = Decimal(max(1, int(getattr(service, 'min_replicas', 1) or 1)))
                summary['cpu_hours'] += Decimal(service.cpu_cores) * replicas * hours_running
                summary['memory_gb_hours'] += (Decimal(service.memory_mb) / Decimal(1024)) * replicas * hours_running

        # Round to 2 decimal places
        summary['cpu_hours'] = summary['cpu_hours'].quantize(Decimal('0.01'))
        summary['memory_gb_hours'] = summary['memory_gb_hours'].quantize(Decimal('0.01'))

        return summary

    def calculate_cost(self, user: object, period_start: object, period_end: object) -> Decimal:
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
            price = self._get_price('CPU', DEFAULT_RESOURCE_PRICES['CPU'])
            cost += cpu_overage * price

        # 2. RAM Overage
        # Plan limit is MB, convert to GB
        allowed_ram_gb_hours = (Decimal(plan.max_memory_mb) / Decimal(1024)) * duration_hours
        ram_overage = max(Decimal(0), summary['memory_gb_hours'] - allowed_ram_gb_hours)
        if ram_overage > 0:
            price = self._get_price('RAM', DEFAULT_RESOURCE_PRICES['RAM'])
            cost += ram_overage * price

        # 3. Storage Overage
        # Storage is a snapshot, not time-based in this simple model (avg usage would be better but snapshot is safer for now)
        allowed_storage = Decimal(plan.max_storage_gb)
        storage_overage = max(Decimal(0), summary['storage_gb'] - allowed_storage)
        if storage_overage > 0:
            price = self._get_price('STORAGE', DEFAULT_RESOURCE_PRICES['STORAGE'])
            cost += storage_overage * price

        return cost.quantize(Decimal('0.01'))

    def _get_price(self, resource_type: str, default: Decimal | None = None) -> Decimal:
        """Return the unit price from ResourcePrice DB, falling back cleanly if unseeded.

        NOTE: ResourcePrice.price_per_unit_monthly is a monthly price.
        For CPU and RAM (hourly resources) the DB monthly value is divided
        by 720 hours/month. Fallback values in ``DEFAULT_RESOURCE_PRICES``
        are already in the caller's target unit.
        """
        fallback = default if default is not None else DEFAULT_RESOURCE_PRICES.get(resource_type, Decimal('0.00'))
        rp = ResourcePrice.objects.filter(resource_type=resource_type, is_active=True).first()
        if rp is not None and rp.price_per_unit_monthly is not None:
            if resource_type in ('CPU', 'RAM'):
                return rp.price_per_unit_monthly / Decimal(720)
            return rp.price_per_unit_monthly
        return fallback

    def check_quota(self, user: object, resource_type: str, requested_amount: int | Decimal = 1) -> tuple[bool, int]:
        """Check if user can use more of a resource. Returns (allowed, remaining)."""
        in_atomic = transaction.get_connection().in_atomic_block
        if in_atomic:
            # Serialize concurrent quota checks on the user's BillingAccount / subscription.
            BillingAccount.objects.select_for_update().filter(user=user).first()
            sub = (
                UserSubscription.objects
                .select_for_update()
                .select_related('plan')
                .filter(user=user)
                .first()
            )
        else:
            sub = UserSubscription.objects.select_related('plan').filter(user=user).first()

        if sub is None or sub.status not in ('ACTIVE', 'TRIAL'):
            return False, 0
        plan = sub.plan

        deleted_service_states = [Service.Status.DELETED, Service.Status.DELETION_PENDING]
        deleted_addon_states = [Addon.Status.DELETED, Addon.Status.DELETION_PENDING]

        if resource_type == 'SERVICE':
            current = Service.objects.filter(owner=user).exclude(status__in=deleted_service_states).count()
            limit = plan.max_services
            if limit < 0:
                return True, -1
            return (current + requested_amount <= limit), max(0, limit - current)

        elif resource_type == 'CPU':
            services = Service.objects.filter(owner=user).exclude(status__in=deleted_service_states)
            current = sum(
                (s.cpu_cores or 0) * max(1, int(getattr(s, 'min_replicas', 1) or 1))
                for s in services
            )
            limit = plan.max_cpu_cores
            if limit < 0:
                return True, -1
            return (current + requested_amount <= limit), max(0, limit - current)

        elif resource_type == 'MEMORY':
            services = Service.objects.filter(owner=user).exclude(status__in=deleted_service_states)
            current = sum(
                (s.memory_mb or 0) * max(1, int(getattr(s, 'min_replicas', 1) or 1))
                for s in services
            )
            limit = plan.max_memory_mb
            if limit < 0:
                return True, -1
            return (current + requested_amount <= limit), max(0, limit - current)

        elif resource_type == 'STORAGE':
            current = Volume.objects.filter(service__owner=user).exclude(
                service__status__in=deleted_service_states
            ).aggregate(total=Sum('size_gb'))['total'] or 0
            limit = plan.max_storage_gb
            if limit < 0:
                return True, -1
            return (current + requested_amount <= limit), max(0, limit - current)

        elif resource_type == 'ADDON':
            current = Addon.objects.filter(service__owner=user).exclude(
                service__status__in=deleted_service_states
            ).exclude(
                status__in=deleted_addon_states
            ).count()
            limit = plan.max_addons
            if limit < 0:
                return True, -1
            return (current + requested_amount <= limit), max(0, limit - current)

        return True, 9999
