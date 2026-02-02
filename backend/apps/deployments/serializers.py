from rest_framework import serializers
from .models import Service, Deployment, EnvironmentVariable

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

    class Meta:
        model = Service
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'owner', 'verification_token']

    def create(self, validated_data):
        env_vars_data = validated_data.pop('env_vars', [])
        service = Service.objects.create(**validated_data)
        for env in env_vars_data:
            EnvironmentVariable.objects.create(service=service, **env)
        return service

class DeploymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Deployment
        fields = '__all__'

class DeploymentTriggerSerializer(serializers.Serializer):
    service_id = serializers.UUIDField()
    provider_id = serializers.UUIDField()
    commit_hash = serializers.CharField(required=False, allow_blank=True)

    # Optional overrides
    cpu_cores = serializers.DecimalField(max_digits=4, decimal_places=2, required=False)
    memory_mb = serializers.IntegerField(required=False)
