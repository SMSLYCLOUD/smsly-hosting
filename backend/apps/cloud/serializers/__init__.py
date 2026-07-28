"""Serializers module."""
from rest_framework import serializers

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
