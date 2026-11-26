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
        from apps.intelligence.providers import SYSTEM_PROMPT, _cached_ask, get_configured_providers
        from apps.intelligence.views import (
            _check_user_cap,
            _check_user_cap_for_committee,
            _record_usage,
            _release_cap_reservation,
        )

        message = request.data.get('message')
        if not message:
            return Response({"error": "Message required"},
                            status=status.HTTP_400_BAD_REQUEST)

        # SECURITY: Input length limit to prevent abuse
        if len(message) > 2000:
            return Response(
                {"error": "Message too long. Maximum 2000 characters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        configured = get_configured_providers()
        reserve = max(64, len(str(message)) // 4 + 128)
        cap_ok, cap_reason = (
            _check_user_cap_for_committee(request.user, len(configured), reserve_tokens=reserve)
            if len(configured) >= 2
            else _check_user_cap(request.user, reserve_tokens=reserve)
        )
        if not cap_ok:
            return Response(
                {"error": cap_reason, "code": "ai_cap_exceeded"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            logger.info("AI chat message from user %s", request.user.id)
            response, provider_name = _cached_ask(
                prompt=message,
                system_prompt=SYSTEM_PROMPT,
                cache_bypass=True,
            )
            _record_usage(
                request.user,
                provider_name,
                provider_name,
                {},
                prompt_text=str(message),
                response_text=str(response or ""),
            )
            return Response({
                "text": response,
                "provider": provider_name,
            })
        except Exception as e:
            logger.error("AI chat error: %s", e)
            return Response(
                {"error": "AI chat temporarily unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        finally:
            _release_cap_reservation(request.user, reserve)
