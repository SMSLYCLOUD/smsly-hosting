from rest_framework.exceptions import PermissionDenied
from apps.billing.services.metering import UsageMeter

class QuotaEnforcementMiddleware:
    """
    Checks user quotas before allowing resource creation.
    Ideally called in Views or Serializers.
    """
    def __init__(self):
        self.meter = UsageMeter()

    def check_service_limit(self, user):
        allowed, remaining = self.meter.check_quota(user, 'SERVICE')
        if not allowed:
            raise PermissionDenied("Service limit reached for your plan. Please upgrade to create more services.")

    def check_addon_limit(self, user):
        allowed, remaining = self.meter.check_quota(user, 'ADDON')
        if not allowed:
            raise PermissionDenied("Addon limit reached for your plan. Please upgrade to create more addons.")

    def check_storage_limit(self, user, requested_gb=1):
        allowed, remaining = self.meter.check_quota(user, 'STORAGE', requested_gb)
        if not allowed:
            raise PermissionDenied(f"Storage limit reached. You have {remaining}GB remaining.")
