from rest_framework import serializers

from ..models import Deployment
from ..models.audit import AuditLog


class DeploymentSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    service_name = serializers.CharField(
        source='service.name', read_only=True)

    class Meta:
        model = Deployment
        fields = '__all__'


class DeploymentTimelineSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    service_name = serializers.CharField(
        source='service.name', read_only=True)

    class Meta:
        model = Deployment
        fields = [
            'id', 'service', 'service_name', 'commit_hash',
            'commit_message', 'status', 'is_rollback',
            'ai_diagnosis',
            'started_at', 'finished_at', 'duration_seconds',
            'created_at',
        ]


class DeploymentTriggerSerializer(serializers.Serializer):
    service_id = serializers.UUIDField()
    provider_id = serializers.UUIDField()
    commit_hash = serializers.CharField(required=False, allow_blank=True)
    skip_review = serializers.BooleanField(default=False)

    cpu_cores = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False)
    memory_mb = serializers.IntegerField(required=False)

    registry_url = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Custom registry URL for this deployment. "
                  "If set, a new Project is auto-created and the "
                  "registry is scoped to it.")
    registry_username = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Username for custom registry authentication")
    registry_password = serializers.CharField(
        required=False, allow_blank=True, write_only=True,
        help_text="Password for custom registry authentication")


class InstantRollbackSerializer(serializers.Serializer):
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
    cpu_cores = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False,
        help_text="Override CPU cores")
    memory_mb = serializers.IntegerField(
        required=False,
        help_text="Override memory in MB")
    env_overrides = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        help_text="Dict of env var key→value to add/update before build")
