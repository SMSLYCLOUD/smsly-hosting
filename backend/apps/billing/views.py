"""Views module."""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .services.stripe import StripeService
from .models import UsageRecord, BillingAccount
from .serializers import CheckoutSessionSerializer, PortalSessionSerializer
from django.db.models import Sum


class CheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            url = StripeService.create_checkout_session(request.user.id)
            return Response({'url': url})
        except Exception as e:
            return Response({'error': str(e)}, status=500)


class SimulateBillingView(APIView):
    """
    Mock endpoint to simulate SaaS billing calculations.
    Returns the estimated cost for the current user's services.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Calculate total usage for user's services
        # Logic: Sum(cost) from UsageRecord where service__owner = user
        total_cost = UsageRecord.objects.filter(
            service__owner=user
        ).aggregate(total=Sum('cost'))['total'] or 0.00

        # Breakdown by service
        services = []
        for service in user.services.all():
            service_cost = service.usage_records.aggregate(
                total=Sum('cost'))['total'] or 0.00
            services.append({
                "service_name": service.name,
                "cost": float(service_cost),
                "cpu_usage_hours": service.usage_records.count()  # Simplified metric
            })

        return Response({
            "currency": "USD",
            "total_estimated_cost": float(total_cost),
            "billing_period": "Current Month",
            "services": services,
            "simulation_mode": True
        })


class CreateCheckoutSessionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CheckoutSessionSerializer(data=request.data)
        if serializer.is_valid():
            # mock Stripe logic
            account, _ = BillingAccount.objects.get_or_create(user=request.user)
            
            # Real impl would use stripe.checkout.Session.create(...)
            # Returning mock URL for now
            return Response({
                'checkout_url': 'https://checkout.stripe.com/test-session-123' 
            })
        return Response(serializer.errors, status=500)

class PortalSessionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PortalSessionSerializer(data=request.data)
        if serializer.is_valid():
             # mock Stripe logic
            return Response({
                'portal_url': 'https://billing.stripe.com/p/login/test'
            })
        return Response(serializer.errors, status=500)
