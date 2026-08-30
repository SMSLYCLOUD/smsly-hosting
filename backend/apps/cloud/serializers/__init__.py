"""Serializers module."""
from rest_framework import serializers

from apps.deployments.models import EcosystemPlan
from ..models import CloudProvider, CloudResource, Secret


class CloudProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = CloudProvider
        fields = [
            'id',
            'name',
            'provider_type',
            'region',
            'project_id',
            'scope',
            'is_active',
            'created_at']
        read_only_fields = ['id', 'created_at']


class CloudProviderCreateSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=False)
    api_secret = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = CloudProvider
        fields = [
            'name',
            'provider_type',
            'region',
            'project_id',
            'tenant_id',
            'scope',
            'api_key',
            'api_secret']


class CloudResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CloudResource
        fields = [
            'id', 'provider', 'resource_id', 'resource_type',
            'name', 'region', 'status', 'metadata',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SecretSerializer(serializers.ModelSerializer):
    class Meta:
        model = Secret
        fields = ['id', 'name', 'provider', 'arn', 'updated_at']


class EcosystemPlanSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = EcosystemPlan
        fields = [
            'id', 'status', 'project', 'selected_repos', 'ai_provider',
            'use_shared_addons', 'cancel_others_on_failure',
            'services_created', 'error_message',
            'created_at', 'updated_at', 'completed_at',
        ]
        read_only_fields = fields


class EcosystemPlanDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = EcosystemPlan
        fields = [
            'id', 'status', 'project', 'selected_repos', 'ai_provider',
            'use_shared_addons', 'cancel_others_on_failure', 'shared_addon_config',
            'scan_task_id', 'deploy_task_id',
            'plan', 'scan_progress',
            'services_created', 'error_message',
            'created_at', 'updated_at', 'completed_at',
        ]
        read_only_fields = fields
