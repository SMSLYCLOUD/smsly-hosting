"""Views Webhooks module — GitHub, GitLab, and Bitbucket webhook receivers."""
import logging
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.conf import settings
from .webhooks.github import GitHubWebhookHandler, _check_duplicate_delivery
from .webhooks.gitlab import GitLabWebhookHandler
from .webhooks.bitbucket import BitbucketWebhookHandler

logger = logging.getLogger(__name__)


class WebhookSchemaSerializer(serializers.Serializer):
    """Schema placeholder for webhook endpoints."""


class WebhookRateThrottle(permissions.BasePermission):
    """Placeholder — real throttling is done by the webhook provider's retry."""
    pass


class GitHubWebhookView(GenericAPIView):
    serializer_class = WebhookSchemaSerializer
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        webhook_secret = getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')
        if not webhook_secret:
            logger.error("GITHUB_WEBHOOK_SECRET not configured — rejecting webhook")
            return Response({'error': 'Webhook processing unavailable'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        handler = GitHubWebhookHandler()
        if not handler.verify_signature(request):
            return Response({'error': 'Invalid signature'},
                            status=status.HTTP_403_FORBIDDEN)

        event_type = request.headers.get('X-GitHub-Event', '')
        delivery_id = request.headers.get('X-GitHub-Delivery', '')
        _, should_process = _check_duplicate_delivery(delivery_id, event_type)
        if not should_process:
            return Response({'status': 'duplicate', 'delivery_id': delivery_id})

        from apps.licensing.models import PlatformLicense
        tier_gates_disabled = bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False))
        if PlatformLicense.load().is_community and not tier_gates_disabled:
            return Response({'message': 'Auto-deploy disabled in Community tier', 'triggered': False})

        try:
            result = handler.handle_event(event_type, request.data, delivery_id=delivery_id)
            return Response({'message': 'Webhook processed', 'triggered': result})
        except Exception as e:
            logger.exception("Webhook processing failed: %s", e)
            return Response({'error': 'Webhook processing failed'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GitLabWebhookView(GenericAPIView):
    serializer_class = WebhookSchemaSerializer
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        webhook_secret = getattr(settings, 'GITLAB_WEBHOOK_SECRET', '')
        if not webhook_secret:
            logger.error("GITLAB_WEBHOOK_SECRET not configured — rejecting webhook")
            return Response({'error': 'Webhook processing unavailable'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        handler = GitLabWebhookHandler()
        if not handler.verify_signature(request):
            return Response({'error': 'Invalid signature'},
                            status=status.HTTP_403_FORBIDDEN)

        event_type = request.headers.get('X-Gitlab-Event', '')
        if not event_type:
            return Response({'error': 'Missing X-Gitlab-Event header'},
                            status=status.HTTP_400_BAD_REQUEST)

        delivery_id = request.headers.get('X-Gitlab-Delivery', '')
        from .webhooks.gitlab import _check_duplicate_delivery as _check_gitlab
        _, should_process = _check_gitlab(delivery_id, event_type)
        if not should_process:
            return Response({'status': 'duplicate', 'delivery_id': delivery_id})

        from apps.licensing.models import PlatformLicense
        tier_gates_disabled = bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False))
        if PlatformLicense.load().is_community and not tier_gates_disabled:
            return Response({'message': 'Auto-deploy disabled in Community tier', 'triggered': False})

        try:
            result = handler.handle_event(event_type, request.data, delivery_id=delivery_id)
            return Response({'message': 'Webhook processed', 'triggered': result})
        except Exception as e:
            logger.exception("GitLab webhook processing failed: %s", e)
            return Response({'error': 'Webhook processing failed'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BitbucketWebhookView(GenericAPIView):
    serializer_class = WebhookSchemaSerializer
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        webhook_secret = getattr(settings, 'BITBUCKET_WEBHOOK_SECRET', '')
        if not webhook_secret:
            logger.error("BITBUCKET_WEBHOOK_SECRET not configured — rejecting webhook")
            return Response({'error': 'Webhook processing unavailable'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        handler = BitbucketWebhookHandler()
        if not handler.verify_signature(request):
            return Response({'error': 'Invalid signature'},
                            status=status.HTTP_403_FORBIDDEN)

        event_type = request.headers.get('X-Event-Key', '')
        if not event_type:
            return Response({'error': 'Missing X-Event-Key header'},
                            status=status.HTTP_400_BAD_REQUEST)

        delivery_id = request.headers.get('X-Request-UUID', '')
        from .webhooks.bitbucket import _check_duplicate_delivery as _check_bitbucket
        _, should_process = _check_bitbucket(delivery_id, event_type)
        if not should_process:
            return Response({'status': 'duplicate', 'delivery_id': delivery_id})

        from apps.licensing.models import PlatformLicense
        tier_gates_disabled = bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False))
        if PlatformLicense.load().is_community and not tier_gates_disabled:
            return Response({'message': 'Auto-deploy disabled in Community tier', 'triggered': False})

        try:
            result = handler.handle_event(event_type, request.data, delivery_id=delivery_id)
            return Response({'message': 'Webhook processed', 'triggered': result})
        except Exception as e:
            logger.exception("Bitbucket webhook processing failed: %s", e)
            return Response({'error': 'Webhook processing failed'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
