from rest_framework import serializers

class CheckoutSessionSerializer(serializers.Serializer):
    plan_id = serializers.CharField()
    success_url = serializers.URLField()
    cancel_url = serializers.URLField()

class PortalSessionSerializer(serializers.Serializer):
    return_url = serializers.URLField()
