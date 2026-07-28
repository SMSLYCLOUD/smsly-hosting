"""registry views."""
import logging

logger = logging.getLogger(__name__)



from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from ..models.registry import RegistryCredential
from ..serializers import RegistryCredentialSerializer
class RegistryCredentialViewSet(viewsets.ModelViewSet):
    serializer_class = RegistryCredentialSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return RegistryCredential.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=['post'])
    def test_connection(self, request, pk=None):
        credential = self.get_object()
        try:
            import docker
            client = docker.from_env()
            result = client.login(
                username=credential.username,
                password=credential.password,
                registry=credential.registry_url,
            )
            return Response({'status': 'success', 'message': result.get('Status', 'Login succeeded')})
        except Exception:
            logger.exception("Registry connection test failed")
            return Response({'status': 'error', 'message': 'Connection test failed. Please verify your credentials.'}, status=400)


