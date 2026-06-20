
class QuotaEnforcementMiddleware:
    """
    Checks user quotas before allowing resource creation.
    Ideally called in Views or Serializers.
    All limits disabled for self-hosted instances.
    """
    def __init__(self):
        pass

    def check_service_limit(self, user):
        return

    def check_addon_limit(self, user):
        return

    def check_storage_limit(self, user, requested_gb=1):
        return
