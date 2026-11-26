"""Custom serializers extending dj-rest-auth's defaults with RBAC data."""

from __future__ import annotations

import logging

from apps.permissions.utils import get_user_permissions
from dj_rest_auth.serializers import UserDetailsSerializer
from django.contrib.auth import get_user_model
from rest_framework import serializers

logger = logging.getLogger(__name__)

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
    is_staff = serializers.ReadOnlyField()
    is_superuser = serializers.ReadOnlyField()

    class Meta(UserDetailsSerializer.Meta):
        fields = (*UserDetailsSerializer.Meta.fields, 'permissions', 'roles', 'is_staff', 'is_superuser')

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
        except Exception as exc:
            logger.debug("Failed to load team memberships: %s", exc)

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
        except Exception as exc:
            logger.debug("Failed to load org memberships: %s", exc)

        return {
            'teams': teams_out,
            'orgs': orgs_out,
        }

    def validate_email(self, value: str) -> str:
        email = (value or '').strip()
        if not email:
            return email
        qs = User.objects.filter(email__iexact=email)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A user with this email address already exists.")
        return email

    def update(self, instance, validated_data):
        old_email = (getattr(instance, 'email', '') or '').strip().lower()
        user = super().update(instance, validated_data)
        new_email = (getattr(user, 'email', '') or '').strip()
        if 'email' in validated_data and new_email and new_email.lower() != old_email:
            try:
                from allauth.account.models import EmailAddress
                EmailAddress.objects.filter(user=user, primary=True).update(primary=False)
                EmailAddress.objects.update_or_create(
                    user=user,
                    email__iexact=new_email,
                    defaults={
                        'email': new_email,
                        'primary': True,
                        'verified': False,
                    },
                )
            except Exception as exc:
                logger.debug("Failed to sync allauth EmailAddress on profile update: %s", exc)
        return user
