import logging
from rest_framework import serializers
from .models import Service, Deployment, EnvironmentVariable, Region
from .models_audit import AuditLog
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .serializers_transfer import ServerTransferSerializer, ServerTransferCreateSerializer


class RegionSerializer(serializers.ModelSerializer):
    """Serializer for Regions."""
    class Meta:
        model = Region
        fields = '__all__'


logger = logging.getLogger(__name__)


class EnvVarSerializer(serializers.ModelSerializer):
    """
    Serializer for Environment Variables.
    Renamed from EnvironmentVariableSerializer to match view import.
    """
    class Meta:
        model = EnvironmentVariable
        fields = ['id', 'key', 'value', 'is_secret', 'source']

    def to_representation(self, instance):
        try:
            ret = super().to_representation(instance)
        except Exception as exc:  # pragma: no cover - depends on corrupted DB data
            logger.error(
                "Failed to serialize env var id=%s key=%s service_id=%s: %s",
                getattr(instance, "id", None),
                getattr(instance, "key", None),
                getattr(instance, "service_id", None),
                exc,
            )
            ret = {
                'id': getattr(instance, 'id', None),
                'key': getattr(instance, 'key', ''),
                'value': '',
                'is_secret': bool(getattr(instance, 'is_secret', False)),
                'source': getattr(instance, 'source', 'USER'),
            }
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
    service_url = serializers.SerializerMethodField()
    project_name = serializers.CharField(
        source='project.name', read_only=True, default=None)
    project_slug = serializers.CharField(
        source='project.slug', read_only=True, default=None)
    project_emoji = serializers.CharField(
        source='project.icon_emoji', read_only=True, default=None)

    class Meta:
        model = Service
        fields = '__all__'
        read_only_fields = [
            'id',
            'created_at',
            'updated_at',
            'owner',
            'verification_token']

    def get_service_url(self, obj: Service) -> str:
        """Railway-style auto-generated URL."""
        if obj.public_domain:
            return f"https://{obj.public_domain}"
        slug = obj.name.lower().replace(' ', '-')
        base_domain = Service.default_public_base_domain()
        return f"https://{slug}.{base_domain}"

    def get_latest_deployment(self, obj: Service) -> dict | None:
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
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    service_name = serializers.CharField(
        source='service.name', read_only=True)

    class Meta:
        model = Deployment
        fields = '__all__'


class DeploymentTimelineSerializer(serializers.ModelSerializer):
    """Lightweight serializer for deployment timeline view."""
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
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


class DeploymentApproveSerializer(serializers.Serializer):
    """Accept optional overrides when approving a deployment review."""
    cpu_cores = serializers.DecimalField(
        max_digits=4, decimal_places=2, required=False,
        help_text="Override CPU cores (0.25–4.0)")
    memory_mb = serializers.IntegerField(
        required=False,
        help_text="Override memory in MB (256–8192)")
    env_overrides = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        help_text="Dict of env var key→value to add/update before build")

class ServiceBackupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceBackup
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'completed_at', 'status', 'file_path', 'size_bytes', 'metadata', 'error_message']

class ServerBackupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerBackup
        fields = '__all__'
        read_only_fields = ['id', 'status', 'file_path', 'size_bytes', 'services_included', 'created_at', 'completed_at']

class BackupScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupSchedule
        fields = '__all__'

