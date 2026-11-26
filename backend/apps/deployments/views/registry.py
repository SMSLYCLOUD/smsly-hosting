"""registry views."""
import logging

logger = logging.getLogger(__name__)



from django.core.exceptions import ValidationError as DjValidationError
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.core.validators import validate_ssrf
from ..models.registry import RegistryCredential
from ..serializers import RegistryCredentialSerializer
class RegistryCredentialViewSet(viewsets.ModelViewSet):
    serializer_class = RegistryCredentialSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return RegistryCredential.objects.filter(owner=self.request.user).exclude(
            name__startswith="__node_sync__:"
        ).order_by('-created_at', 'id')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=['post'])
    def test_connection(self, request, pk=None):
        credential = self.get_object()
        url = (credential.registry_url or '').strip()
        if url:
            check_target = url if '://' in url else f'https://{url}'
            is_admin = bool(request.user and (request.user.is_staff or request.user.is_superuser))
            if not is_admin:
                if '://' in url and url.lower().startswith('http://'):
                    return Response(
                        {'status': 'error', 'message': 'Insecure http:// registry URLs are not permitted.'},
                        status=400,
                    )
                try:
                    validate_ssrf(check_target, fail_closed_dns=True)
                except DjValidationError as exc:
                    return Response({'status': 'error', 'message': str(exc)}, status=400)
        try:
            import docker
            client = docker.from_env(timeout=15)
            result = client.login(
                username=credential.username,
                password=credential.password,
                registry=credential.registry_url,
            )
            return Response({'status': 'success', 'message': result.get('Status', 'Login succeeded')})
        except Exception:
            logger.exception("Registry connection test failed")
            return Response({'status': 'error', 'message': 'Connection test failed. Please verify your credentials.'}, status=400)


