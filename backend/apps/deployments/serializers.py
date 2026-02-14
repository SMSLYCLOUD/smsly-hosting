from rest_framework import serializers
from .models import Service, Deployment, EnvironmentVariable, Region
from .models_audit import AuditLog


class RegionSerializer(serializers.ModelSerializer):
    """Serializer for Regions."""
    class Meta:
        model = Region
        fields = '__all__'


class EnvVarSerializer(serializers.ModelSerializer):
    """
    Serializer for Environment Variables.
    Renamed from EnvironmentVariableSerializer to match view import.
    """
    class Meta:
        model = EnvironmentVariable
        fields = ['id', 'key', 'value', 'is_secret']

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        # Mask secret values
        if instance.is_secret:
            ret['value'] = '********'
        return ret


class ServiceSerializer(serializers.ModelSerializer):
    env_vars = EnvVarSerializer(many=True, required=False)
    regions = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Region.objects.all(), required=False)
    primary_region = serializers.PrimaryKeyRelatedField(
        queryset=Region.objects.all(), required=False)
    latest_deployment = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = '__all__'
        read_only_fields = [
            'id',
            'created_at',
            'updated_at',
            'owner',
            'verification_token']

    def get_latest_deployment(self, obj):
        dep = obj.deployments.order_by('-created_at').first()
        if not dep:
            return None
        return {
            'id': str(dep.id),
            'status': dep.status,
            'commit_hash': dep.commit_hash or '',
            'created_at': dep.created_at.isoformat() if dep.created_at else None,
        }

    def create(self, validated_data):
        env_vars_data = validated_data.pop('env_vars', [])
        regions_data = validated_data.pop('regions', [])
        
        service = Service.objects.create(**validated_data)
        
        for env in env_vars_data:
            EnvironmentVariable.objects.create(service=service, **env)
            
        if regions_data:
            service.regions.set(regions_data)
            
        return service

    def update(self, instance, validated_data):
        regions_data = validated_data.pop('regions', None)
        instance = super().update(instance, validated_data)
        
        if regions_data is not None:
            instance.regions.set(regions_data)
            
        return instance


class DeploymentSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.ReadOnlyField()
    service_name = serializers.CharField(
        source='service.name', read_only=True)

    class Meta:
        model = Deployment
        fields = '__all__'


class DeploymentTimelineSerializer(serializers.ModelSerializer):
    """Lightweight serializer for deployment timeline view."""
    duration_seconds = serializers.ReadOnlyField()
    service_name = serializers.CharField(
        source='service.name', read_only=True)

    class Meta:
        model = Deployment
        fields = [
            'id', 'service', 'service_name', 'commit_hash',
            'commit_message', 'status', 'is_rollback',
            'started_at', 'finished_at', 'duration_seconds',
            'created_at',
        ]


class DeploymentTriggerSerializer(serializers.Serializer):
    service_id = serializers.UUIDField()
    provider_id = serializers.UUIDField()
    commit_hash = serializers.CharField(required=False, allow_blank=True)

    # Optional overrides
    cpu_cores = serializers.DecimalField(
        max_digits=4, decimal_places=2, required=False)
    memory_mb = serializers.IntegerField(required=False)


class InstantRollbackSerializer(serializers.Serializer):
    """Serializer for instant rollback — no body required, but allows
    an optional message."""
    message = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
        help_text="Optional reason for rollback")


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = '__all__'
