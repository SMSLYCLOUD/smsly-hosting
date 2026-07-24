from rest_framework import serializers

from ..models import Invoice, PricingPlan, ResourcePrice, UsageRecord, UserSubscription


class PricingPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingPlan
        fields = '__all__'

class ResourcePriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourcePrice
        fields = '__all__'

class UserSubscriptionSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source='plan.name', read_only=True)

    class Meta:
        model = UserSubscription
        fields = '__all__'
        read_only_fields = ('user', 'status', 'current_period_start', 'current_period_end', 'stripe_subscription_id', 'trial_ends_at')

class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = '__all__'
        read_only_fields = ('user', 'subscription', 'status', 'period_start', 'period_end', 'subtotal', 'tax', 'total', 'line_items')

class UsageRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = UsageRecord
        fields = '__all__'

class CheckoutSessionSerializer(serializers.Serializer):
    plan_id = serializers.CharField()
    success_url = serializers.URLField()
    cancel_url = serializers.URLField()

class PortalSessionSerializer(serializers.Serializer):
    return_url = serializers.URLField()
