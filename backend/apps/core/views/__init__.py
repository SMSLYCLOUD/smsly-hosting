"""Contact form API — stores messages for admin review."""
import logging
import os
import secrets

from apps.deployments.models import Deployment, Service
from apps.deployments.models.addons import Addon
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from ..models import APIKey

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

        # Services — count by status in a single aggregated query (no N+1)
        total = Service.objects.filter(owner=user).count()
        running = Service.objects.filter(owner=user, status=Service.Status.ACTIVE).count()
        failed = Service.objects.filter(owner=user, status=Service.Status.DELETION_FAILED).count()
        stopped = Service.objects.filter(
            owner=user,
            status__in=[Service.Status.DELETION_PENDING, Service.Status.DELETED],
        ).count()
        unknown = total - running - failed - stopped

        service_stats = {
            "total": total,
            "running": running,
            "failed": failed,
            "stopped": stopped,
            "unknown": max(0, unknown),
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
        from apps.billing.services.metering import UsageMeter
        meter = UsageMeter()
        cost = meter.calculate_cost(user, start_of_month, now)

        # Resource Usage (Aggregated)
        usage_summary = meter.get_usage_summary(user, start_of_month, now)

        # Recent Activity (Last 10 deployments)
        recent_activity = Deployment.objects.filter(service__owner=user).order_by('-created_at')[:10].values(
            'id', 'service__name', 'status', 'commit_message', 'created_at'
        )

        # Security Alerts (Zero-Trust checks)
        alerts = []
        if user.is_superuser and user.username == 'admin':
            # Check for common default passwords
            common_defaults = ['admin', 'admin123', 'smsly-admin', 'password']
            env_default = os.getenv('DJANGO_SUPERUSER_PASSWORD')
            if env_default:
                common_defaults.append(env_default)

            is_default = any(user.check_password(p) for p in common_defaults)
            if is_default:
                alerts.append({
                    "id": "default_password",
                    "type": "warning",
                    "title": "Default password detected",
                    "message": "You are using a default or insecure admin password. Please change it immediately.",
                    "action_url": "/settings",
                    "action_text": "Change Password"
                })

        payload = {
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
            "alerts": alerts
        }

        # Host-level system metrics are admin-only — they expose
        # infrastructure details (total RAM, disk size, CPU) that
        # regular tenants should not see.
        if user.is_superuser:
            import psutil
            vm = psutil.virtual_memory()
            try:
                disk = psutil.disk_usage('/')
                storage_used_gb = disk.used / (1024**3)
                storage_total_gb = disk.total / (1024**3)
            except Exception:
                storage_used_gb = 0
                storage_total_gb = 0
            payload["system_usage"] = {
                "ram_used_mb": int(vm.used / (1024 * 1024)),
                "ram_total_mb": int(vm.total / (1024 * 1024)),
                "storage_used_gb": round(storage_used_gb, 2),
                "storage_total_gb": round(storage_total_gb, 2),
                "cpu_percent": psutil.cpu_percent(interval=None),
            }

        return Response(payload)


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
    queryset = APIKey.objects.all().order_by('-created_at')
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


class SubdomainStubViewSet(viewsets.ViewSet):
    """Stub endpoint for /api/v1/subdomains/ until full implementation."""
    permission_classes = [IsAuthenticated]

    def list(self, request):
        return Response({'subdomains': [], 'limit': 0})

    def create(self, request):
        return Response(
            {'detail': 'Subdomain reservations coming soon.'},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def destroy(self, request, pk=None):
        return Response(status=status.HTTP_404_NOT_FOUND)


class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'is_active', 'is_staff', 'is_superuser', 'date_joined']
        read_only_fields = ['id', 'username', 'email', 'is_staff', 'is_superuser', 'date_joined']


class AdminUserViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet
):
    """Admin-only endpoint to manage users."""
    permission_classes = [IsAdminUser]
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = AdminUserSerializer

class SystemResourcesView(GenericAPIView):
    """Fetch physical host system limits for resource bounding."""
    serializer_class = EmptySerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        import psutil
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()

        # Round up RAM slightly to handle odd manufacturer sizes (e.g., 11.7GB -> 12GB)
        # But we'll just send exactly what we have in MB
        ram_mb = int(vm.total / (1024 * 1024))
        swap_mb = int(sm.total / (1024 * 1024))

        # CPU cores
        cpu_cores = psutil.cpu_count(logical=True) or 1

        return Response({
            "cpu_cores": cpu_cores,
            "ram_mb": ram_mb,
            "swap_mb": swap_mb
        })

from .observability import ObservabilityRateThrottle, grafana_embed_url, loki_query, loki_label_values, prometheus_query
from .throttled_auth import ThrottledLoginView, ThrottledLogoutView, ThrottledPasswordResetView, ThrottledRegistrationView
