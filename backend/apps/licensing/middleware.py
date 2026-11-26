import logging
import re

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

logger = logging.getLogger(__name__)

_TEAM_INVITE_RE = re.compile(r"^/api/v1/teams/[^/]+/invite_member/?$")
_PROJECT_INVITE_RE = re.compile(r"^/api/v1/projects/[^/]+/members/invite/?$")
_ORG_INVITE_RE = re.compile(r"^/api/v1/organizations/[^/]+/invite/?$")


class TierLimitsMiddleware:
    """
    Enforce platform license tier limits (services, team members) when
    ``SMSLY_DISABLE_TIER_GATES`` is False.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _effective_limits(self):
        from apps.licensing.models import PlatformLicense

        license_obj = PlatformLicense.load()
        is_expired = bool(license_obj.expires_at and license_obj.expires_at <= timezone.now())
        if not license_obj.is_valid or is_expired:
            return license_obj, 3, 1
        return license_obj, int(license_obj.max_services), int(license_obj.max_team_members)

    def __call__(self, request):
        if bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False)):
            return self.get_response(request)

        path = request.path or ""
        method = (request.method or "GET").upper()

        if method == "POST":
            if path.rstrip("/") == "/api/v1/services":
                try:
                    license_obj, max_services, _ = self._effective_limits()
                    request.platform_license = license_obj
                    if max_services >= 0:
                        from apps.deployments.models import Service
                        current_services = Service.objects.exclude(
                            status__in=[Service.Status.DELETED, Service.Status.DELETION_PENDING]
                        ).count()
                        if current_services >= max_services:
                            return JsonResponse(
                                {
                                    "error": (
                                        f"Platform license service limit ({max_services}) reached. "
                                        "Upgrade your license tier to create more services."
                                    )
                                },
                                status=403,
                            )
                except Exception:
                    logger.exception("TierLimitsMiddleware service limit check failed")
                    return JsonResponse(
                        {"error": "Unable to verify platform license limits."},
                        status=403,
                    )

            elif (
                _TEAM_INVITE_RE.match(path)
                or _PROJECT_INVITE_RE.match(path)
                or _ORG_INVITE_RE.match(path)
            ):
                try:
                    license_obj, _, max_members = self._effective_limits()
                    request.platform_license = license_obj
                    if max_members >= 0:
                        from django.contrib.auth import get_user_model
                        from django.db.models import Q
                        User = get_user_model()
                        if User.objects.filter(is_active=True).count() >= max_members:
                            from apps.organizations.models import OrganizationMembership, ProjectMember
                            from apps.teams.models import TeamMember
                            now = timezone.now()
                            unexpired_q = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
                            team_user_ids = set(
                                TeamMember.objects.filter(unexpired_q, is_active=True, user__is_active=True)
                                .values_list("user_id", flat=True)
                            )
                            org_user_ids = set(
                                OrganizationMembership.objects.filter(unexpired_q, is_active=True, user__is_active=True)
                                .values_list("user_id", flat=True)
                            )
                            project_user_ids = set(
                                ProjectMember.objects.filter(unexpired_q, user__is_active=True)
                                .values_list("user_id", flat=True)
                            )
                            active_members = len(team_user_ids | org_user_ids | project_user_ids)
                            if active_members >= max_members:
                                return JsonResponse(
                                    {
                                        "error": (
                                            f"Platform license team member limit ({max_members}) reached. "
                                            "Upgrade your license tier to invite more members."
                                        )
                                    },
                                    status=403,
                                )
                except Exception:
                    logger.exception("TierLimitsMiddleware team member limit check failed")
                    return JsonResponse(
                        {"error": "Unable to verify platform license limits."},
                        status=403,
                    )

        return self.get_response(request)

