from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import PlatformLicense
from ..serializers import LicenseActivationSerializer, LicenseStatusSerializer
from ..validator import parse_offline_license_data, validate_license


class LicenseViewSet(viewsets.ViewSet):
    """
    API for managing Platform License.
    """
    permission_classes = [permissions.IsAdminUser]

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def status(self, request):
        """Get current license status and enabled features."""
        license_obj = PlatformLicense.load()
        tier_gates_disabled = bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False))
        if (
            not tier_gates_disabled
            and license_obj.is_valid
            and license_obj.expires_at
            and license_obj.expires_at <= timezone.now()
        ):
            validate_license(license_obj, force_fresh=True)
            license_obj.refresh_from_db()

        serializer = LicenseStatusSerializer(license_obj)
        data = dict(serializer.data)

        # Owner edition: explicit runtime bypass for all tier gates/UI locks.
        if tier_gates_disabled:
            data.update(
                {
                    "tier": "enterprise",
                    "is_valid": True,
                    "is_community": False,
                    "is_pro": True,
                    "is_enterprise": True,
                    "max_services": -1,
                    "max_team_members": -1,
                }
            )
            data["features"] = {
                "ai_features": True,
                "autoscaler": True,
                "custom_domains": True,
                "ssl_certificates": True,
                "marketplace": True,
                "functions": True,
                "tunnels": True,
                "topology": True,
                "transfers": True,
                "backups_automated": True,
                "sso": True,
                "audit_logs": True,
                "white_label": True,
                "rbac": True,
                "multi_node": True,
            }

        return Response(data)

    @action(detail=False, methods=['post'])
    def activate(self, request):
        """Submit a license key to activate Pro/Enterprise features."""
        serializer = LicenseActivationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data['license_key'].strip()
        raw_license_data = request.data.get('license_data') if hasattr(request, 'data') else None
        parsed_license_data = parse_offline_license_data(key, raw_license_data)

        license_obj = PlatformLicense.load()
        license_obj.license_key = key
        license_obj.license_data = parsed_license_data
        license_obj.is_valid = False
        license_obj.validation_error = ''
        license_obj.save()

        # Trigger fresh validation immediately (bypassing any prior grace period)
        try:
            validate_license(license_obj, force_fresh=True)
        except Exception:
            # Already handled inside validator logging
            pass

        license_obj.refresh_from_db()

        is_expired = bool(license_obj.expires_at and license_obj.expires_at <= timezone.now())
        if not license_obj.is_valid or is_expired:
            return Response({
                'status': 'error',
                'message': 'License validation failed',
                'detail': license_obj.validation_error or ('License has expired' if is_expired else 'Invalid license'),
                'license': LicenseStatusSerializer(license_obj).data
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'status': 'activated',
            'message': f'License activated successfully. Tier: {license_obj.get_tier_display()}',
            'license': LicenseStatusSerializer(license_obj).data
        })

    @action(detail=False, methods=['post'])
    def deactivate(self, request):
        """Remove license key and downgrade to Community."""
        license_obj = PlatformLicense.load()
        license_obj.license_key = ''
        license_obj.license_data = ''
        license_obj.tier = 'community'
        license_obj.is_valid = False
        license_obj.validation_error = ''
        license_obj.save()

        return Response({
            'status': 'deactivated',
            'message': 'License removed. Platform downgraded to Community tier.',
            'license': LicenseStatusSerializer(license_obj).data
        })
