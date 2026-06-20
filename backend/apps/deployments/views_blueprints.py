"""Views Blueprints module."""
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.cloud.models import CloudProvider
from apps.deployments.services.blueprint_manager import BlueprintManager


class BlueprintPayloadSerializer(serializers.Serializer):
    data = serializers.JSONField(required=False)  # type: ignore[assignment]


class BlueprintViewSet(viewsets.GenericViewSet):
    serializer_class = BlueprintPayloadSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """List available blueprints."""
        # Hardcoded for now, could scan directory
        blueprints = [
            {
                "id": "smsly-ecosystem",
                "name": "SMSLY Full Ecosystem",
                "description": "Deploy the complete 30-service stack.",
                "icon": "layers"
            }
        ]
        return Response(blueprints)

    @action(detail=False, methods=['post'])
    def deploy(self, request):
        """
        Deploy a blueprint.
        POST /api/v1/blueprints/deploy/
        Body: { "blueprint_id": "smsly-ecosystem", "provider_id": "..." }
        """
        # M-6 fix: blueprint deployment is admin-only since CloudProvider
        # is a global resource (no per-user ownership)
        if not request.user.is_staff:
            return Response(
                {'error': 'Only admins can deploy blueprints.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        blueprint_id = request.data.get('blueprint_id')
        provider_id = request.data.get('provider_id')

        try:
            provider = CloudProvider.objects.get(id=provider_id)
            manager = BlueprintManager(provider, request.user)
            manager.deploy(blueprint_id)
            return Response(
                {'message': 'Blueprint deployment started'}, status=status.HTTP_202_ACCEPTED)
        except CloudProvider.DoesNotExist:
            return Response({'error': 'Provider not found'},
                            status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
