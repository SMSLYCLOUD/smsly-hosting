"""mTLS reload actions for the service viewset."""
import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from apps.teams.permissions import assert_can_write

logger = logging.getLogger(__name__)


class MtlsActionsMixin:
    """Manual mTLS reload: redeploy from HEAD + refresh all components."""

    @action(detail=True, methods=['post'], url_path='mtls-reload',
            throttle_classes=[BurstRateThrottle, DeploymentRateThrottle])
    def mtls_reload(self, request, pk=None):
        """
        Manually reload mTLS for one service.
        POST /api/v1/services/{id}/mtls-reload/
        Body: {} (redeploys from HEAD)

        Steps: normal manual redeploy (latest GitHub state) + Envoy
        sidecar refresh on the running container + SPIRE entries sync.
        Services without an enabled MtlsConfig are rejected (400).
        """
        from apps.deployments.views.service.deploy import DeployActionsMixin
        from apps.mtls.services.reload import _mtls_config_for, reload_service_mtls

        service = self.get_object()
        assert_can_write(self.request.user, service)
        if _mtls_config_for(service) is None:
            return Response(
                {'error': 'mTLS is not enabled for this service.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deploy_response = DeployActionsMixin.deploy(self, request, pk=pk)
        result = reload_service_mtls(service, deploy_response)
        code = 200 if result["deploy_triggered"] else deploy_response.status_code
        return Response(result, status=code)
