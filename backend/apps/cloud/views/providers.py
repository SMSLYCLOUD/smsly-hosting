"""Views module."""
import logging
import re
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.cloud.models import CloudProvider, CloudResource
from apps.cloud.serializers import (
    CloudProviderCreateSerializer,
    CloudProviderSerializer,
    CloudResourceSerializer,
)

logger = logging.getLogger(__name__)


_CREDENTIAL_LITERAL_RE = re.compile(
    r'(\s*)([a-z_][a-z0-9_]*_password)(\s*=\s*)"[^"]+"',
    re.IGNORECASE,
)


def _strip_literal_credentials(template: str) -> str:
    """Replace any literal password value in the template with a
    ``var.<name>`` reference so the rendered IaC never carries a cleartext
    credential the user could accidentally commit or apply.
    """
    return _CREDENTIAL_LITERAL_RE.sub(r'\1\2\3var.\2', template)


class CloudProviderViewSet(viewsets.ModelViewSet):
    # M-3 fix: non-admin users only see active providers (no credential details)
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CloudProviderSerializer

    def get_permissions(self):
        if self.action in {'create', 'update', 'partial_update', 'destroy'}:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        # Only return active providers for regular users
        if self.request.user.is_staff:
            return CloudProvider.objects.all().order_by('id')
        return CloudProvider.objects.filter(is_active=True).order_by('id')

    def get_serializer_class(self):
        if self.action == 'create':
            return CloudProviderCreateSerializer
        return CloudProviderSerializer

    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        """
        Validate provider connectivity and refresh provider activation state.
        """
        provider = self.get_object()

        if not request.user.is_staff:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        try:
            from apps.cloud.services.compute import ComputeService
            compute_service = ComputeService(provider)
            authenticated = bool(compute_service.adapter.authenticate())
        except NotImplementedError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except Exception as exc:
            return Response(
                {'error': f'Sync failed: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        provider.is_active = authenticated
        provider.save(update_fields=['is_active', 'updated_at'])

        resource_count = CloudResource.objects.filter(provider=provider).count()
        return Response({
            'status': 'synced' if authenticated else 'auth_failed',
            'provider_id': str(provider.id),
            'provider_type': provider.provider_type,
            'is_active': provider.is_active,
            'resource_count': resource_count,
        })

    @action(detail=True, methods=['post'])
    def validate_credentials(self, request, pk=None):
        provider = self.get_object()

        # Only admins can trigger validation
        if not request.user.is_staff:
            return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

        try:
            from apps.cloud.services.compute import ComputeService
            compute_service = ComputeService(provider)
            adapter = compute_service.adapter
            is_valid = adapter.authenticate()
            return Response({
                'status': 'success' if is_valid else 'failed',
                'message': 'Credentials are valid' if is_valid else 'Authentication failed'
            })
        except Exception as e:
            # Mask the exact error if it contains sensitive keys
            error_msg = str(e)
            if provider.api_key and provider.api_key in error_msg:
                error_msg = error_msg.replace(provider.api_key, '***')
            if provider.api_secret and provider.api_secret in error_msg:
                error_msg = error_msg.replace(provider.api_secret, '***')

            return Response({
                'status': 'error',
                'message': f'Validation failed: {error_msg}'
            }, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def available_regions(self, request):
        """
        Return known deployable regions by provider.

        Query params:
          - provider_type (optional): AWS|GCP|AZURE|LOCAL|RAILWAY|VERCEL
        """
        catalog = {
            CloudProvider.ProviderType.AWS: [
                {'id': 'af-south-1', 'name': 'Cape Town'},
                {'id': 'eu-west-2', 'name': 'London'},
                {'id': 'eu-central-1', 'name': 'Frankfurt'},
                {'id': 'us-east-1', 'name': 'N. Virginia'},
            ],
            CloudProvider.ProviderType.GCP: [
                {'id': 'europe-west1', 'name': 'Belgium'},
                {'id': 'europe-west3', 'name': 'Frankfurt'},
                {'id': 'us-central1', 'name': 'Iowa'},
                {'id': 'us-east1', 'name': 'South Carolina'},
            ],
            CloudProvider.ProviderType.AZURE: [
                {'id': 'westeurope', 'name': 'West Europe'},
                {'id': 'uksouth', 'name': 'UK South'},
                {'id': 'eastus', 'name': 'East US'},
                {'id': 'southafricanorth', 'name': 'South Africa North'},
            ],
            CloudProvider.ProviderType.LOCAL: [
                {'id': 'local', 'name': 'Local Cluster'},
            ],
            CloudProvider.ProviderType.RAILWAY: [
                {'id': 'us-west', 'name': 'US West'},
                {'id': 'eu-west', 'name': 'EU West'},
            ],
            CloudProvider.ProviderType.VERCEL: [
                {'id': 'iad1', 'name': 'Washington, D.C.'},
                {'id': 'cdg1', 'name': 'Paris'},
                {'id': 'sin1', 'name': 'Singapore'},
            ],
        }

        provider_type = (request.query_params.get('provider_type') or '').strip().upper()
        if provider_type:
            if provider_type not in catalog:
                return Response(
                    {'error': f'Unknown provider_type: {provider_type}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            rows = catalog[provider_type]
            return Response([{'provider': provider_type, **row} for row in rows])

        rows = []
        for provider_key, provider_regions in catalog.items():
            for row in provider_regions:
                rows.append({'provider': provider_key, **row})
        return Response(rows)


class CloudResourceViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = CloudResourceSerializer
    queryset = CloudResource.objects.all().order_by('id')


