"""Contact form API — stores messages for admin review."""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import AllowAny
import logging

logger = logging.getLogger(__name__)


class ContactSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    email = serializers.EmailField()
    company = serializers.CharField(max_length=200, required=False, allow_blank=True)
    message = serializers.CharField(max_length=5000)


class ContactView(APIView):
    """Accept contact form submissions. No auth required."""
    permission_classes = [AllowAny]
    throttle_scope = 'contact'

    def post(self, request):
        serializer = ContactSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        logger.info(
            "Contact form submission: name=%s email=%s company=%s message_length=%d",
            data['name'], data['email'], data.get('company', ''), len(data['message'])
        )
        # Future: save to DB or send email notification
        return Response({"detail": "Message received. We'll get back to you within 24 hours."}, status=status.HTTP_201_CREATED)
