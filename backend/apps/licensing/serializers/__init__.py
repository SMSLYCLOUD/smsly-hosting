from rest_framework import serializers

from ..models import PlatformLicense


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
        from django.conf import settings
        if bool(getattr(settings, 'SMSLY_DISABLE_TIER_GATES', False)):
            return {
                'ai_features': True,
                'autoscaler': True,
                'custom_domains': True,
                'ssl_certificates': True,
                'marketplace': True,
                'functions': True,
                'tunnels': True,
                'topology': True,
                'transfers': True,
                'backups_automated': True,
                'sso': True,
                'audit_logs': True,
                'white_label': True,
                'rbac': True,
                'multi_node': True,
            }
        is_pro = bool(getattr(obj, 'is_valid', False) and getattr(obj, 'is_pro', False))
        is_ent = bool(getattr(obj, 'is_valid', False) and getattr(obj, 'is_enterprise', False))
        return {
            'ai_features': is_pro,
            'autoscaler': is_pro,
            'custom_domains': True,
            'ssl_certificates': True,
            'marketplace': True,
            'functions': is_pro,
            'tunnels': is_pro,
            'topology': is_pro,
            'transfers': is_pro,
            'backups_automated': is_pro,
            'sso': is_ent,
            'audit_logs': is_pro,
            'white_label': is_ent,
            'rbac': is_pro,
            'multi_node': is_ent,
        }

    def to_representation(self, instance):
        from django.conf import settings
        data = super().to_representation(instance)
        if bool(getattr(settings, 'SMSLY_DISABLE_TIER_GATES', False)):
            data['tier'] = 'enterprise'
            data['is_valid'] = True
            data['is_community'] = False
            data['is_pro'] = True
            data['is_enterprise'] = True
            data['max_services'] = -1
            data['max_team_members'] = -1
        return data

class LicenseActivationSerializer(serializers.Serializer):
    license_key = serializers.CharField(required=True)

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return instance
