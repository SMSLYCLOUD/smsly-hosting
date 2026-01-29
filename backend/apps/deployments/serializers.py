from rest_framework import serializers
from .models import Service, Deployment, EnvironmentVariable, ComplianceProfile

class ComplianceProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceProfile
        fields = ['hipaa_compliant', 'gdpr_compliant', 'soc2_compliant', 'data_residency']

class EnvironmentVariableSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnvironmentVariable
        fields = ['id', 'key', 'value', 'is_secret', 'created_at']
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {'value': {'write_only': True}} # Hide value on read for secrets

class DeploymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Deployment
        fields = [
            'id', 'service', 'commit_hash', 'commit_message',
            'status', 'started_at', 'finished_at', 'created_at', 'ai_diagnosis', 'vulnerability_report'
        ]
        read_only_fields = ['status', 'started_at', 'finished_at', 'ai_diagnosis', 'vulnerability_report']

class DeploymentDetailSerializer(DeploymentSerializer):
    class Meta(DeploymentSerializer.Meta):
        fields = DeploymentSerializer.Meta.fields + ['build_logs', 'runtime_logs_url', 'ai_diagnosis', 'vulnerability_report']

class ServiceSerializer(serializers.ModelSerializer):
    compliance = ComplianceProfileSerializer(required=False)
    env_vars = EnvironmentVariableSerializer(many=True, read_only=True)
    latest_deployment = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = [
            'id', 'name', 'repository_url', 'branch',
            'build_command', 'start_command', 'root_directory',
            'internal_port', 'public_domain', 'domain_verified', 'verification_token',
            'cpu_cores', 'memory_mb',
            'min_replicas', 'max_replicas', 'autoscale_cpu_target', 'use_blue_green',
            'is_preview', 'pr_number', 'previews',
            'compliance', 'env_vars', 'latest_deployment', 'deployments',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['domain_verified', 'verification_token', 'previews']

    deployments = DeploymentSerializer(many=True, read_only=True)
    previews = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    def get_latest_deployment(self, obj):
        deployment = obj.deployments.first()
        if deployment:
            return DeploymentSerializer(deployment).data
        return None

    def create(self, validated_data):
        compliance_data = validated_data.pop('compliance', None)
        service = Service.objects.create(**validated_data)
        if compliance_data:
            ComplianceProfile.objects.create(service=service, **compliance_data)
        else:
            ComplianceProfile.objects.create(service=service)
        return service

    def update(self, instance, validated_data):
        compliance_data = validated_data.pop('compliance', None)
        if compliance_data:
            ComplianceProfile.objects.update_or_create(service=instance, defaults=compliance_data)
        return super().update(instance, validated_data)
