"""Contact form API — stores messages for admin review."""
from rest_framework import viewsets, mixins
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework import status, serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
import logging
from django.utils import timezone
from apps.deployments.models import Service, Deployment
from apps.deployments.models_addons import Addon
from apps.billing.services.metering import UsageMeter
from .models import APIKey
import secrets
from django.contrib.auth.hashers import make_password

logger = logging.getLogger(__name__)


class ContactSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    email = serializers.EmailField()
    company = serializers.CharField(max_length=200, required=False, allow_blank=True)
    message = serializers.CharField(max_length=5000)


class EmptySerializer(serializers.Serializer):
    """Schema placeholder for response-only APIViews."""


class ContactView(GenericAPIView):
    """Accept contact form submissions. No auth required."""
    serializer_class = ContactSerializer
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


class DashboardOverviewView(GenericAPIView):
    serializer_class = EmptySerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Services
        services = Service.objects.filter(owner=user)
        # Count running services: services that have an ACTIVE deployment
        running_services = services.filter(deployments__status='ACTIVE').distinct().count()

        service_stats = {
            "total": services.count(),
            "running": running_services,
            "failed": 0, # Placeholder or complex query
            "stopped": services.count() - running_services
        }

        # Deployments this month
        deployments_this_month = Deployment.objects.filter(
            service__owner=user,
            created_at__gte=start_of_month
        ).count()

        # Addons
        addons = Addon.objects.filter(service__owner=user)
        addon_stats = {
            "total": addons.count(),
            "active": addons.filter(status='ACTIVE').count()
        }

        # Cost Estimate
        meter = UsageMeter()
        cost = meter.calculate_cost(user, start_of_month, now)

        # Resource Usage (Aggregated)
        usage_summary = meter.get_usage_summary(user, start_of_month, now)

        # Recent Activity (Last 10 deployments)
        recent_activity = Deployment.objects.filter(service__owner=user).order_by('-created_at')[:10].values(
            'id', 'service__name', 'status', 'commit_message', 'created_at'
        )

        return Response({
            "services": service_stats,
            "deployments_this_month": deployments_this_month,
            "addons": addon_stats,
            "cost_estimate": {"monthly_usd": cost, "currency": "USD"},
            "resource_usage": {
                "cpu_hours": usage_summary['cpu_hours'],
                "memory_gb_hours": usage_summary['memory_gb_hours'],
                "storage_gb": usage_summary['storage_gb'],
                "bandwidth_gb": usage_summary['bandwidth_gb']
            },
            "recent_activity": recent_activity,
            "alerts": []
        })


class APIKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = APIKey
        fields = ['id', 'name', 'prefix', 'last_used', 'created_at']
        read_only_fields = ['id', 'prefix', 'last_used', 'created_at']


class APIKeyViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    queryset = APIKey.objects.all()
    serializer_class = APIKeySerializer

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)

    def list(self, request):
        return super().list(request)

    def create(self, request):
        name = request.data.get('name', 'My API Key')
        raw_key = f"sk_{secrets.token_urlsafe(32)}"
        prefix = raw_key[:8]
        key_hash = make_password(raw_key)

        api_key = APIKey.objects.create(
            user=request.user,
            name=name,
            key_hash=key_hash,
            prefix=prefix
        )

        return Response({
            'id': api_key.id,
            'name': api_key.name,
            'prefix': api_key.prefix,
            'key': raw_key  # Returned once
        }, status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        key = self.get_queryset().filter(pk=pk).first()
        if key:
            key.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(status=status.HTTP_404_NOT_FOUND)
