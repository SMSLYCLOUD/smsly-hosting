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
        # All features unlocked (self-hosted mode)
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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Always report as enterprise with unlimited resources (self-hosted)
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
