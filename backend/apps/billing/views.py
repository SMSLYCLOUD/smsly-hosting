"""Views module."""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .services.stripe import StripeService


class CheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            url = StripeService.create_checkout_session(request.user.id)
            return Response({'url': url})
        except Exception as e:
            return Response({'error': str(e)}, status=500)
