"""Views Chat module."""
import logging

from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.rate_limiting import AIChatRateThrottle

logger = logging.getLogger(__name__)


class AIChatSchemaSerializer(serializers.Serializer):
    """Schema placeholder for AI chat endpoint."""


class AIChatView(GenericAPIView):
    serializer_class = AIChatSchemaSerializer
    permission_classes = [IsAuthenticated]  # SECURITY: Require authentication
    throttle_classes = [AIChatRateThrottle]

    def post(self, request):
        from apps.intelligence.providers import SYSTEM_PROMPT, _cached_ask
        message = request.data.get('message')
        if not message:
            return Response({"detail": "Message required"},
                            status=status.HTTP_400_BAD_REQUEST)

        # SECURITY: Input length limit to prevent abuse
        if len(message) > 2000:
            return Response(
                {"detail": "Message too long. Maximum 2000 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            logger.info("AI chat message from user %s", request.user.id)
            response, provider_name = _cached_ask(
                prompt=message,
                system_prompt=SYSTEM_PROMPT,
                cache_bypass=True,
            )
            return Response({
                "text": response,
                "provider": provider_name,
            })
        except Exception as e:
            logger.error("AI chat error: %s", e)
            return Response(
                {"detail": "AI chat temporarily unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
