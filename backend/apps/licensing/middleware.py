import logging
from django.utils import timezone
from django.http import JsonResponse
from django.urls import resolve
from .models import PlatformLicense

logger = logging.getLogger(__name__)

class TierLimitsMiddleware:
    """
    Enforce tier-based limits on resource creation.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST':
            path = request.path

            # Use resolve() to get view name if possible, or just string match
            # String match is simpler for middleware without importing views

            # 1. Service Limit Check
            # Endpoint: /api/v1/deployments/services/ (creation)
            if '/api/v1/deployments/services/' in path and 'verify' not in path and request.method == 'POST':
                error = self._check_service_limit(request)
                if error: return error

            # 2. Deployment Limit Check
            # Endpoints: /api/v1/deployments/deploy/ or redeploy actions
            if ('/api/v1/deployments/' in path and '/deploy/' in path) or ('/redeploy/' in path):
                error = self._check_deployment_limit(request)
                if error: return error

            # 3. Team Member Limit Check
            # Endpoints: /api/v1/teams/{id}/members/ or invitations
            if '/api/v1/teams/' in path and ('/members/' in path or '/invitations/' in path):
                 error = self._check_team_member_limit(request)
                 if error: return error

        response = self.get_response(request)
        return response

    def _check_service_limit(self, request):
        try:
            from apps.deployments.models import Service
            license = PlatformLicense.load()

            # Community limit: 3 services
            if license.is_community:
                # Count total services on the platform instance
                count = Service.objects.count()
                if count >= license.max_services:
                    return JsonResponse({
                        'error': 'limit_reached',
                        'detail': f'Service limit reached ({license.max_services}). Upgrade to Pro for unlimited services.',
                        'current_tier': license.tier,
                        'upgrade_url': '/settings/billing'
                    }, status=402)
        except Exception as e:
            logger.error(f"Error checking service limit: {e}")
            pass
        return None

    def _check_deployment_limit(self, request):
        try:
            from apps.deployments.models import Deployment
            license = PlatformLicense.load()

            if license.is_community:
                # Limit: 5 per day
                today = timezone.now().date()
                # Count deployments created today
                count = Deployment.objects.filter(created_at__date=today).count()
                if count >= 5:
                    return JsonResponse({
                        'error': 'limit_reached',
                        'detail': 'Daily deployment limit reached (5/day). Upgrade to Pro for unlimited deployments.',
                        'current_tier': license.tier,
                        'upgrade_url': '/settings/billing'
                    }, status=402)
        except Exception as e:
            logger.error(f"Error checking deployment limit: {e}")
            pass
        return None

    def _check_team_member_limit(self, request):
        try:
            from apps.teams.models import TeamMember
            license = PlatformLicense.load()

            # Limit: 1 (owner) for Community, 5 for Pro
            limit = license.max_team_members

            # This is tricky because "Community: 1 (owner only)" usually means you cannot add *anyone*.
            # If limit is 1, and owner is already 1, then count >= limit blocks new adds.
            # We count total users across all teams or just members?
            # Assuming "Team members" means total unique users excluding superuser maybe?
            # Or simpler: total TeamMember records. But a user can be in multiple teams?
            # Let's count total distinct users in TeamMember table.

            current_members = TeamMember.objects.values('user').distinct().count()

            # However, the owner creates the first team and is a member. So count is 1.
            # So if limit is 1, they cannot add more. Correct.

            if current_members >= limit:
                 return JsonResponse({
                    'error': 'limit_reached',
                    'detail': f'Team member limit reached ({limit}). Upgrade to increase limits.',
                    'current_tier': license.tier,
                    'upgrade_url': '/settings/billing'
                }, status=402)

        except Exception as e:
            logger.error(f"Error checking team limit: {e}")
            pass
        return None
