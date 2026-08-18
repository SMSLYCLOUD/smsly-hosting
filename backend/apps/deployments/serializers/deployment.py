from rest_framework import serializers

from ..models import Deployment
from ..models.audit import AuditLog


class DeploymentSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)
    service_name = serializers.CharField(
        source='service.name', read_only=True)
    target_server_name = serializers.SerializerMethodField()

    class Meta:
        model = Deployment
        fields = [
            'id', 'service', 'service_name',
            'commit_hash', 'commit_message', 'branch', 'status',
            'build_logs', 'runtime_logs_url', 'pipeline_stages',
            'ai_diagnosis', 'review_summary', 'vulnerability_report',
            'container_id', 'remote_deployment_id', 'green_container_id',
            'staging_url', 'staged_at',
            'started_at', 'finished_at', 'duration_seconds',
            'is_rollback', 'source_node', 'rollback_from',
            'target_server', 'target_server_name', 'target_is_local',
            'ecosystem_retry_count', 'queued_min_replicas',
            'metadata', 'registry_override',
            'verified_target_type', 'verified_host_ip',
            'verified_runtime_id', 'verified_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'container_id', 'remote_deployment_id',
            'green_container_id', 'staging_url', 'staged_at',
            'target_server', 'target_is_local',
            'verified_target_type', 'verified_host_ip',
            'verified_runtime_id', 'verified_at',
            'started_at', 'finished_at', 'created_at', 'updated_at',
        ]

    def get_target_server_name(self, obj):
        if obj.target_server_id:
            return getattr(obj.target_server, 'name', None)
        if obj.target_is_local:
            return 'Local Server'
        return None


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
        fields = [
            'id', 'timestamp', 'user', 'project',
            'actor', 'action', 'target', 'metadata',
        ]
        read_only_fields = ['id', 'timestamp', 'user', 'actor', 'metadata']


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
