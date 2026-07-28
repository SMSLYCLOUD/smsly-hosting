from rest_framework import serializers

from ..models import Invoice, PricingPlan, ResourcePrice, UsageRecord, UserSubscription


class PricingPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingPlan
        fields = [
            'id', 'name', 'slug', 'description', 'is_active', 'sort_order',
            'max_services', 'max_cpu_cores', 'max_memory_mb',
            'max_storage_gb', 'max_bandwidth_gb', 'max_addons',
            'max_custom_domains', 'max_team_members',
            'has_auto_scaling', 'has_priority_support', 'has_backup',
            'has_server_transfer', 'has_advanced_metrics', 'has_ai_diagnosis',
            'price_monthly_usd', 'price_yearly_usd',
            'stripe_price_id_monthly', 'stripe_price_id_yearly',
            'flutterwave_plan_id',
        ]

class ResourcePriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourcePrice
        fields = [
            'id', 'resource_type', 'price_per_unit_monthly',
            'unit_label', 'is_active',
        ]

class UserSubscriptionSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source='plan.name', read_only=True)

    class Meta:
        model = UserSubscription
        fields = [
            'id', 'user', 'plan', 'plan_name', 'status', 'billing_cycle',
            'current_period_start', 'current_period_end',
            'stripe_subscription_id', 'trial_ends_at',
        ]
        read_only_fields = ('user', 'status', 'current_period_start', 'current_period_end', 'stripe_subscription_id', 'trial_ends_at')

class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = [
            'id', 'user', 'subscription', 'status',
            'period_start', 'period_end',
            'subtotal', 'tax', 'total', 'line_items',
            'pdf_url', 'paid_at', 'due_date',
        ]
        read_only_fields = ('user', 'subscription', 'status', 'period_start', 'period_end', 'subtotal', 'tax', 'total', 'line_items')

class UsageRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = UsageRecord
        fields = [
            'id', 'service', 'timestamp',
            'cpu_cores', 'memory_mb', 'duration_seconds', 'cost',
        ]
        read_only_fields = ['id', 'timestamp']

class CheckoutSessionSerializer(serializers.Serializer):
    plan_id = serializers.CharField()
    success_url = serializers.URLField()
    cancel_url = serializers.URLField()

class PortalSessionSerializer(serializers.Serializer):
    return_url = serializers.URLField()
