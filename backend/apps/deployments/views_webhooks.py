"""Views Webhooks module."""
import logging
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from .webhooks.github import GitHubWebhookHandler, _check_duplicate_delivery

logger = logging.getLogger(__name__)


class WebhookSchemaSerializer(serializers.Serializer):
    """Schema placeholder for GitHub webhook endpoint."""


class GitHubWebhookView(GenericAPIView):
    serializer_class = WebhookSchemaSerializer
    authentication_classes = []  # Webhooks use signature auth
    permission_classes = []

    def post(self, request):
        handler = GitHubWebhookHandler()

        # ZH-003 FIX: Signature verification is MANDATORY (fail-closed).
        # If no secret is configured, reject ALL webhooks.
        webhook_secret = getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')
        if not webhook_secret:
            logger.error("GITHUB_WEBHOOK_SECRET is not configured - rejecting webhook")
            return Response({'error': 'Webhook processing unavailable'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        if not handler.verify_signature(request):
            return Response({'error': 'Invalid signature'},
                            status=status.HTTP_403_FORBIDDEN)

        # 2. Parse Event
        event_type = request.headers.get('X-GitHub-Event')
        if not event_type:
            return Response({'error': 'Missing event type'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 2.5 Idempotency: dedup on X-GitHub-Delivery BEFORE any other
        # processing so the same delivery never triggers two builds even
        # across tier gates / license checks.
        delivery_id = request.headers.get('X-GitHub-Delivery', '')
        _, should_process = _check_duplicate_delivery(delivery_id, event_type)
        if not should_process:
            return Response(
                {'status': 'duplicate', 'delivery_id': delivery_id},
                status=status.HTTP_200_OK,
            )

        # 1.5 Check License Tier for auto-deploy
        from apps.licensing.models import PlatformLicense
        tier_gates_disabled = bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False))
        if PlatformLicense.load().is_community and not tier_gates_disabled:
            logger.info("Auto-deploy disabled in Community tier. Ignoring webhook.")
            return Response({'message': 'Auto-deploy disabled in Community tier', 'triggered': False})

        # 3. Handle Logic
        try:
            triggered = handler.handle_event(
                event_type, request.data, delivery_id=delivery_id,
            )
            return Response(
                {'message': 'Webhook processed', 'triggered': triggered})
        except Exception as e:
            # ZH-012 FIX: Never leak exception details to the caller
            logger.exception("Webhook processing failed: %s", e)
            from .models_audit import WebhookDelivery
            if delivery_id:
                WebhookDelivery.objects.filter(
                    delivery_id=delivery_id
                ).update(status='failed')
            return Response({'error': 'Webhook processing failed'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
