"""Centralized team permission utilities with role hierarchy.

Role hierarchy (highest to lowest):
  ADMIN  → full CRUD, manage members
  MEMBER → create/update, cannot delete
  VIEWER → read-only

This module replaces inline Q filters scattered across views with
a single, testable permission layer.
"""
from __future__ import annotations

import logging

from django.db.models import Q

logger = logging.getLogger(__name__)


def _log_denial(user, obj, code: str) -> None:
    """Best-effort audit logging for permission denials raised by assert helpers."""
    try:
        from apps.permissions.models import PermissionDeniedAudit
        PermissionDeniedAudit.objects.create(
            user=user if user and user.is_authenticated else None,
            path='',
            method='INTERNAL',
            permission_code=code,
            resource_id=getattr(obj, 'pk', None),
        )
    except Exception:
        logger.exception("Failed to log permission denial for code=%s", code)


def _team_role(user, service_or_project) -> str | None:
    """Return the user's role on the team that owns this resource.

    Returns one of ``ADMIN``, ``MEMBER``, ``VIEWER``, or ``None``
    if the user is not on the owning team.
    """
    from .models import TeamMember
    team = _resolve_team(service_or_project)
    if not team:
        return None
    membership = TeamMember.objects.filter(
        team=team, user=user, is_active=True,
    ).first()
    if not membership:
        return None
    from django.utils import timezone
    if membership.expires_at and membership.expires_at < timezone.now():
        return None
    return membership.role


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
    from django.utils import timezone

    from .models import TeamMember
    team = _resolve_team(service_or_project)
    if not team:
        return False
    return TeamMember.objects.filter(
        team=team, user=user, is_active=True,
    ).exclude(
        expires_at__isnull=False, expires_at__lt=timezone.now(),
    ).exists()


def get_team_q_filter(user, prefix: str = "", request=None, team_id=None) -> Q:
    """Return a Q filter for queryset scoping.

    Returns a filter that matches resources owned by *user* or
    belonging to a project whose team includes *user* (any role).
    Superusers see all resources (no filtering).
    """
    from django.utils import timezone

    from .models import TeamMember

    if team_id is None and request is not None:
        team_id = (
            request.headers.get('X-Team-ID')
            or request.GET.get('team_id')
            or getattr(request, 'query_params', {}).get('team_id')
        )

    if team_id is not None and str(team_id).strip() != "" and str(team_id).strip().lower() not in ('null', 'undefined', 'none'):
        is_authorized = user.is_superuser or TeamMember.objects.filter(
            team_id=team_id, user=user, is_active=True,
        ).exclude(
            expires_at__isnull=False, expires_at__lt=timezone.now(),
        ).exists()
        if is_authorized:
            return Q(**{f"{prefix}project__team_id": team_id}) | Q(**{f"{prefix}owner": user, f"{prefix}project__isnull": True})

    if user.is_superuser:
        return Q()

    team_ids = TeamMember.objects.filter(
        user=user, is_active=True,
    ).exclude(
        expires_at__isnull=False, expires_at__lt=timezone.now(),
    ).values_list('team_id', flat=True)
    owner_kw = f"{prefix}owner"
    team_kw = f"{prefix}project__team_id__in"
    return Q(**{owner_kw: user}) | Q(**{team_kw: list(team_ids)})



def assert_can_write(user, service_or_project, action='modify'):
    """Raise PermissionError if user cannot write to this resource."""
    from rest_framework.exceptions import PermissionDenied
    if user.is_superuser:
        return
    if getattr(service_or_project, 'owner', None) == user:
        return
    if not user_can_write(user, service_or_project):
        code = f"service.{action.lower().replace(' ', '_')}"
        _log_denial(user, service_or_project, code)
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
        _log_denial(user, service_or_project, 'service.delete')
        raise PermissionDenied(
            'Only the resource owner or a team ADMIN can delete this resource.'
        )
