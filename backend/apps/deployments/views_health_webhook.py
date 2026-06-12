"""Health Webhook API."""
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from apps.deployments.models_core import Service, Deployment

logger = logging.getLogger(__name__)


class ServiceHealthWebhookView(APIView):
    """
    Webhook endpoint for services to report their own health (Push model).
    This bypasses the pull-based health monitor startup grace period.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request, service_id):
        webhook_token = request.headers.get("X-Health-Token") or request.data.get("token")
        if not webhook_token:
            return Response({"error": "Missing health token"}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            service = Service.objects.get(id=service_id)
        except Service.DoesNotExist:
            return Response({"error": "Service not found"}, status=status.HTTP_404_NOT_FOUND)

        if not service.health_webhook_token or service.health_webhook_token != webhook_token:
            return Response({"error": "Invalid health token"}, status=status.HTTP_403_FORBIDDEN)

        health_status = request.data.get("status", "healthy")

        if health_status == "healthy":
            service.health_status = "healthy"
            service.save(update_fields=["health_status", "updated_at"])

            # Advance the deployment if it's waiting
            active_deployments = Deployment.objects.filter(
                service=service,
                status=Deployment.Status.HEALTH_CHECK,
            )
            for deployment in active_deployments:
                deployment.status = Deployment.Status.ACTIVE
                deployment.save(update_fields=["status", "updated_at"])

            logger.info("Service %s reported healthy via webhook. Activated %d deployments.",
                        service.name, active_deployments.count())

            return Response({"message": "Health status updated to healthy"})
        else:
            service.health_status = "unhealthy"
            service.save(update_fields=["health_status", "updated_at"])
            return Response({"message": "Health status updated to unhealthy"})
