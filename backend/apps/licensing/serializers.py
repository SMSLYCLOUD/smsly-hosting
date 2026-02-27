from rest_framework import serializers
from .models import PlatformLicense

class LicenseStatusSerializer(serializers.ModelSerializer):
    is_community = serializers.BooleanField(read_only=True)
    is_pro = serializers.BooleanField(read_only=True)
    is_enterprise = serializers.BooleanField(read_only=True)
    features = serializers.SerializerMethodField()

    class Meta:
        model = PlatformLicense
        fields = [
            'license_key', 'tier', 'is_valid', 'last_validated',
            'licensed_to', 'instance_id', 'expires_at',
            'max_services', 'max_team_members',
            'is_community', 'is_pro', 'is_enterprise',
            'features', 'validation_error'
        ]
        extra_kwargs = {
            'license_key': {'write_only': True}
        }

    def get_features(self, obj):
        is_pro = obj.tier in ['pro', 'enterprise'] or (obj.tier == 'community' and False)
        is_ent = obj.tier == 'enterprise'

        return {
            'ai_features': is_pro,
            'autoscaler': is_pro,
            'custom_domains': is_pro,
            'ssl_certificates': is_pro,
            'marketplace': is_pro,
            'functions': is_pro,
            'tunnels': is_pro,
            'topology': is_pro,
            'transfers': is_pro,
            'backups_automated': is_pro,
            'sso': is_ent,
            'audit_logs': is_ent,
            'white_label': is_ent,
            'rbac': is_ent,
            'multi_node': is_ent,
        }

class LicenseActivationSerializer(serializers.Serializer):
    license_key = serializers.CharField(required=True)

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return instance
