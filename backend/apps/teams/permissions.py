"""Centralized team permission utilities with role hierarchy.

Role hierarchy (highest to lowest):
  ADMIN  → full CRUD, manage members
  MEMBER → create/update, cannot delete
  VIEWER → read-only

This module replaces inline Q filters scattered across views with
a single, testable permission layer.
"""
from django.db.models import Q


def _team_role(user, service_or_project) -> str | None:
    """Return the user's role on the team that owns this resource.
    
    Returns one of ``ADMIN``, ``MEMBER``, ``VIEWER``, or ``None``
    if the user is not on the owning team.
    """
    from .models import TeamMember
    team = _resolve_team(service_or_project)
    if not team:
        return None
    membership = TeamMember.objects.filter(team=team, user=user).first()
    if membership:
        return membership.role
    return None


def _resolve_team(service_or_project):
    """Resolve the Team from a Service or Project."""
    project = getattr(service_or_project, 'project', None)
    if project:
        team = getattr(project, 'team', None)
        if team:
            return team
    if hasattr(service_or_project, 'team'):
        return service_or_project.team
    return None


def user_is_team_admin(user, service_or_project) -> bool:
    """Can the user perform destructive operations (delete)?"""
    return _team_role(user, service_or_project) == 'ADMIN'


def user_can_write(user, service_or_project) -> bool:
    """Can the user create/update resources (but not delete)?"""
    role = _team_role(user, service_or_project)
    return role in ('ADMIN', 'MEMBER')


def user_can_read(user, service_or_project) -> bool:
    """Can the user view resources?"""
    role = _team_role(user, service_or_project)
    return role in ('ADMIN', 'MEMBER', 'VIEWER')


def user_is_owner_or_team_member(user, service_or_project) -> bool:
    """Legacy check — any team membership (binary)."""
    from .models import Team, TeamMember
    team = _resolve_team(service_or_project)
    if not team:
        return False
    return TeamMember.objects.filter(team=team, user=user).exists()


def get_team_q_filter(user) -> Q:
    """Return a Q filter for queryset scoping.
    
    Returns a filter that matches resources owned by *user* or
    belonging to a project whose team includes *user* (any role).
    """
    from .models import TeamMember
    team_ids = TeamMember.objects.filter(user=user).values_list('team_id', flat=True)
    return Q(owner=user) | Q(project__team_id__in=list(team_ids))


def assert_can_write(user, service_or_project, action='modify'):
    """Raise PermissionError if user cannot write to this resource."""
    from rest_framework.exceptions import PermissionDenied
    if user.is_superuser:
        return
    if getattr(service_or_project, 'owner', None) == user:
        return
    if not user_can_write(user, service_or_project):
        raise PermissionDenied(
            f'You do not have permission to {action} this resource. '
            'Requires ADMIN or MEMBER role on the owning team.'
        )


def assert_can_delete(user, service_or_project):
    """Raise PermissionError if user cannot delete this resource."""
    from rest_framework.exceptions import PermissionDenied
    if user.is_superuser:
        return
    if getattr(service_or_project, 'owner', None) == user:
        return
    if not user_is_team_admin(user, service_or_project):
        raise PermissionDenied(
            'Only the resource owner or a team ADMIN can delete this resource.'
        )
