"""Organization permissions — role hierarchy:

OWNER → full control, can delete org, manage billing
ADMIN → manage members, teams, SSO config
MEMBER → view and use org resources
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from .models import OrganizationMembership


def _active_unexpired_filter():
    return Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())


def get_role(user, organization):
    """Return the user's role in an organization, or None."""
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    if getattr(organization, 'owner_id', None) == getattr(user, 'id', None):
        return 'OWNER'
    membership = (
        OrganizationMembership.objects.filter(
            user=user, organization=organization, is_active=True,
        )
        .filter(_active_unexpired_filter())
        .first()
    )
    return membership.role if membership else None


def is_owner(user, organization):
    return get_role(user, organization) == 'OWNER'


def is_admin(user, organization):
    role = get_role(user, organization)
    return role in ('OWNER', 'ADMIN')


def is_member(user, organization):
    return get_role(user, organization) is not None


def assert_owner(user, organization):
    if not is_owner(user, organization):
        raise PermissionDenied("Only the organization owner can perform this action.")


def assert_admin(user, organization):
    if not is_admin(user, organization):
        raise PermissionDenied("Requires ADMIN or OWNER role in the organization.")


def assert_member(user, organization):
    if not is_member(user, organization):
        raise PermissionDenied("You are not a member of this organization.")


def get_org_from_resource(obj):
    """Walk from a Service/Project/Team to its Organization, or return None."""
    # Service → Project → Team → Organization
    team = getattr(obj, 'team', None)  # Project or Team itself
    if not team:
        project = getattr(obj, 'project', None)
        if project:
            team = getattr(project, 'team', None)
    if not team:
        service = getattr(obj, 'service', None)
        if service:
            team = getattr(getattr(service, 'project', None), 'team', None)
    if not team:
        return None
    return getattr(team, 'organization', None)


def get_org_q_filter(user):
    """Return a Q filter scoping queries to the user's organizations."""
    if not user or not getattr(user, 'is_authenticated', False):
        return Q(pk__in=[])
    org_ids = (
        OrganizationMembership.objects.filter(
            user=user, is_active=True,
        )
        .filter(_active_unexpired_filter())
        .values_list('organization_id', flat=True)
    )
    return Q(id__in=list(org_ids)) | Q(owner=user)
