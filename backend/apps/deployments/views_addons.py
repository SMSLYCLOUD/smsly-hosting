from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models_addons import Addon
from .models import Service, EnvironmentVariable

class AddonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Addon
        fields = ['id', 'service', 'name', 'addon_type', 'status', 'connection_url', 'created_at']
        read_only_fields = ['status', 'connection_url', 'created_at']

class AddonViewSet(viewsets.ModelViewSet):
    queryset = Addon.objects.all()
    serializer_class = AddonSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        addon = serializer.save()
        # Trigger provisioning task (Simulated)
        self._provision_addon(addon)

    def _provision_addon(self, addon):
        """Simulate provisioning and injecting env var."""
        # In real world: Celery task -> Helm install -> Wait -> Get URL

        if addon.addon_type == Addon.Type.POSTGRES:
            url = f"postgres://user:pass@db-{addon.id}:5432/db"
            key = "DATABASE_URL"
        elif addon.addon_type == Addon.Type.REDIS:
            url = f"redis://redis-{addon.id}:6379/0"
            key = "REDIS_URL"
        else:
            return

        addon.status = Addon.Status.ACTIVE
        addon.connection_url = url
        addon.save()

        # Inject into Service Env Vars
        EnvironmentVariable.objects.create(
            service=addon.service,
            key=key,
            value=url,
            is_secret=True
        )
