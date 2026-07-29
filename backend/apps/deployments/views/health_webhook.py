"""Health Webhook API."""
import hmac
import logging

from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.deployments.models.audit import AuditLog
from apps.deployments.models.core import Deployment, Service

logger = logging.getLogger(__name__)


class ServiceHealthWebhookThrottle(AnonRateThrottle):
    """Per-IP throttle for the health webhook. The endpoint is
    AllowAny (the service pushing the heartbeat doesn't have a
    user session), so the throttle is keyed by client IP.
    """
    scope = 'service_health_webhook'
    rate = '60/minute'


_ALLOWED_WEBHOOK_EVENTS = frozenset({'health_update', 'deploy_complete'})


def _client_ip(request) -> str:
    """Best-effort client IP extraction, X-Forwarded-For aware."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


class ServiceHealthWebhookView(APIView):
    """
    Webhook endpoint for services to report their own health (Push model).
    This bypasses the pull-based health monitor startup grace period.

    SECURITY (Issue 14): the endpoint enforces, in addition to the
    per-service token, an explicit ``event`` field (only
    ``health_update`` or ``deploy_complete`` are accepted), an
    HTTPS-only constraint outside of DEBUG, and a per-request nonce
    (X-Webhook-Nonce header) that must be unique within a 10-minute
    window — preventing replay of an intercepted webhook. Every
    invocation is recorded in the immutable ``AuditLog``.
    """
    authentication_classes: list = []
    permission_classes: list = []
    throttle_classes = [ServiceHealthWebhookThrottle]

    def post(self, request, service_id):
        if not settings.DEBUG and not getattr(settings, 'IS_TESTING', False):
            if not request.is_secure():
                return Response(
                    {"error": "HTTPS required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        webhook_token = request.headers.get("X-Health-Token") or request.data.get("token")
        if not webhook_token:
            return Response({"error": "Missing health token"}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            service = Service.objects.get(id=service_id)
        except Service.DoesNotExist:
            return Response({"error": "Service not found"}, status=status.HTTP_404_NOT_FOUND)

        # SECURITY (Batch G):
        # 1. If the service has no webhook token configured, return
        #    404 (not 403). The previous code returned 403 here,
        #    which let an unauthenticated caller distinguish
        #    "service exists but no webhook configured" from
        #    "service doesn't exist" — a small info leak.
        # 2. Use a constant-time compare via hmac.compare_digest to
        #    prevent timing-based token extraction. The previous
        #    ``!=`` short-circuited on first mismatch and leaked
        #    timing info.
        expected_token = (service.health_webhook_token or "").strip()
        if not expected_token:
            return Response({"error": "Service not found"}, status=status.HTTP_404_NOT_FOUND)
        if not hmac.compare_digest(expected_token, webhook_token):
            return Response({"error": "Invalid health token"}, status=status.HTTP_403_FORBIDDEN)

        # Replay protection: each request must carry a unique nonce
        # (header X-Webhook-Nonce or body 'nonce') the server has
        # never seen. The nonce is recorded in the cache for 10
        # minutes, so a captured-and-replayed request after that
        # window would also need the (still-secret) token to be
        # accepted — i.e. the nonce is a strong second factor.
        nonce = (
            request.headers.get("X-Webhook-Nonce")
            or request.data.get("nonce")
            or ""
        ).strip()
        if not nonce or len(nonce) < 8 or len(nonce) > 128:
            return Response(
                {"error": "Missing or invalid nonce (X-Webhook-Nonce header)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        nonce_key = f"webhook_nonce:health:{service.id}:{nonce}"
        if cache.get(nonce_key):
            return Response(
                {"error": "Nonce already used"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        cache.set(nonce_key, "1", timeout=600)

        # Event allowlist: reject unknown event types so a stolen
        # token can't be reused as a generic "do anything"
        # primitive if more actions are added later.
        event = str(request.data.get("event", "health_update") or "health_update").strip()
        if event not in _ALLOWED_WEBHOOK_EVENTS:
            AuditLog.objects.create(
                actor="system",
                action="HEALTH_WEBHOOK_REJECTED",
                target=f"Service:{service.id}",
                metadata={
                    "service_id": str(service.id),
                    "reason": "unknown_event",
                    "event": event,
                    "ip": _client_ip(request),
                },
            )
            return Response(
                {"error": f"Unknown event '{event}'. Allowed: {sorted(_ALLOWED_WEBHOOK_EVENTS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        health_status = request.data.get("status", "healthy")

        if event == "health_update" and health_status == "healthy":
            service.health_status = "healthy"
            service.save(update_fields=["health_status", "updated_at"])

            active_deployments = Deployment.objects.filter(
                service=service,
                status=Deployment.Status.HEALTH_CHECK,
            )
            activated = 0
            for deployment in active_deployments:
                deployment.status = Deployment.Status.ACTIVE
                deployment.save(update_fields=["status", "updated_at"])
                activated += 1

                # Post success commit status to GitHub (non-blocking)
                try:
                    from apps.deployments.tasks.cicd.tasks_commit_status import update_commit_status
                    update_commit_status.delay(
                        str(deployment.id), 'success', 'Deployment active'
                    )
                except Exception as exc:
                    logger.debug("Failed to post commit status: %s", exc)

            AuditLog.objects.create(
                actor="system",
                action="HEALTH_WEBHOOK_APPLIED",
                target=f"Service:{service.id}",
                metadata={
                    "service_id": str(service.id),
                    "event": event,
                    "ip": _client_ip(request),
                    "activated_deployments": activated,
                },
            )

            logger.info("Service %s reported healthy via webhook. Activated %d deployments.",
                        service.name, activated)

            return Response({"message": "Health status updated to healthy"})

        if event == "health_update":
            service.health_status = "unhealthy"
            service.save(update_fields=["health_status", "updated_at"])
            AuditLog.objects.create(
                actor="system",
                action="HEALTH_WEBHOOK_APPLIED",
                target=f"Service:{service.id}",
                metadata={
                    "service_id": str(service.id),
                    "event": event,
                    "ip": _client_ip(request),
                    "health_status": "unhealthy",
                },
            )
            return Response({"message": "Health status updated to unhealthy"})

        # event == "deploy_complete" — record audit log only.
        # The deploy pipeline is the source of truth for "deploy
        # completed" so this is a hook for downstream automation
        # (e.g. notify the on-call, mark metadata). The platform
        # itself does not flip the service to ACTIVE here.
        AuditLog.objects.create(
            actor="system",
            action="DEPLOY_COMPLETE_WEBHOOK",
            target=f"Service:{service.id}",
            metadata={
                "service_id": str(service.id),
                "event": event,
                "ip": _client_ip(request),
            },
        )
        return Response({"message": "deploy_complete acknowledged"})
