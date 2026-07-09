"""Custom serializers extending dj-rest-auth's defaults with RBAC data."""

from __future__ import annotations

from apps.permissions.utils import get_user_permissions
from dj_rest_auth.serializers import UserDetailsSerializer
from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class CustomUserDetailsSerializer(UserDetailsSerializer):
    """Extends the default user details with permissions and role info.

    Added fields:
        permissions: list[str] — all permission codes the user has globally
        is_staff: bool — Django staff flag (included for convenience)
        roles: dict — team and org role summaries

    The ``roles`` dict contains:
        teams: list[dict] — {team_id, team_name, role, can_manage_billing}
        orgs: list[dict] — {org_id, org_name, role, can_manage_billing}

    This lets the frontend determine which UI elements to show without
    making additional API calls for each protected area.
    """

    permissions = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()

    class Meta(UserDetailsSerializer.Meta):
        fields = (*UserDetailsSerializer.Meta.fields, 'permissions', 'roles')

    def get_permissions(self, user) -> list[str]:
        return get_user_permissions(user)

    def get_roles(self, user) -> dict:
        teams_out = []
        orgs_out = []

        try:
            from apps.teams.models import TeamMember
            memberships = TeamMember.objects.filter(
                user=user, is_active=True,
            ).select_related('team')
            for m in memberships:
                teams_out.append({
                    'team_id': str(m.team_id),
                    'team_name': m.team.name,
                    'role': m.role,
                    'can_manage_billing': m.can_manage_billing,
                })
        except Exception:
            pass

        try:
            from apps.organizations.models import OrganizationMembership
            org_memberships = OrganizationMembership.objects.filter(
                user=user, is_active=True,
            ).select_related('organization')
            for m in org_memberships:
                orgs_out.append({
                    'org_id': str(m.organization_id),
                    'org_name': m.organization.name,
                    'role': m.role,
                    'can_manage_billing': m.can_manage_billing,
                })
        except Exception:
            pass

        return {
            'teams': teams_out,
            'orgs': orgs_out,
        }
