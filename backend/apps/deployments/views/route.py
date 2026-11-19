"""route views."""
import logging

logger = logging.getLogger(__name__)



from django.core.cache import cache
from rest_framework import permissions, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from apps.core.rate_limiting import TrustedIdentMixin
from ..models import Deployment
from ._helpers import EmptySerializer, _normalize_request_domain, _service_for_domain
class RouteRecheckView(GenericAPIView):
    """
    Public route recheck hook for fallback pages.

    Allows a domain-level health recheck without requiring a dashboard login.
    This is intentionally rate-limited and only operates on known service domains.
    """

    serializer_class = EmptySerializer
    permission_classes = [permissions.AllowAny]

    def _extract_domain(self, request):
        raw_host = (
            request.query_params.get("host")
            or request.data.get("host")
            or request.get_host()
        )
        host = str(raw_host or "").strip().lower()
        if ":" in host:
            host = host.split(":", 1)[0]
        domain, domain_error = _normalize_request_domain(host)
        if domain_error:
            return None, domain_error
        return domain, None

    def _trigger_recheck(self, service):
        try:
            from apps.core.services.health_monitor import (
                _check_service_health,
                reset_restart_state,
            )

            reset_restart_state(str(service.id))
            _check_service_health(service, Deployment)
            service.refresh_from_db(fields=["health_status"])
            return True, service.health_status
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Route recheck failed for service %s: %s", service.id, exc)
            return False, "unknown"

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)

    def _handle(self, request):
        domain, domain_error = self._extract_domain(request)
        if domain_error:
            return Response(
                {"error": f"Invalid domain: {domain_error}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = _service_for_domain(domain)
        if not service:
            return Response(
                {"error": "Domain is not mapped to a service"},
                status=status.HTTP_404_NOT_FOUND,
            )

        client_ip = TrustedIdentMixin().get_ident(request)
        throttle_key = f"route-recheck:{service.id}:{client_ip}"
        if not cache.add(throttle_key, True, timeout=20):
            return Response(
                {"error": "Recheck already requested. Try again in a few seconds."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        return Response(
            {
                "message": "Health status retrieved",
                "service_id": str(service.id),
                "health_status": service.health_status,
            }
        )


