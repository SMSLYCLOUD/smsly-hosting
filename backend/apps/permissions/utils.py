"""Pure utility functions for permission resolution.

This module is deliberately free of Django REST Framework imports so it can
be called from anywhere — views, consumers, management commands, and tests.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from .codes import (
    ADMIN_ACCESS,
    ALL_PERMISSIONS,
    BILLING_ADMIN,
    BILLING_MANAGE,
    DEFAULT_ROLE_PERMISSIONS,
)

# Custom per-member overrides may never grant platform-admin or account-billing privileges.
ALLOWED_CUSTOM_PERMISSIONS: frozenset[str] = frozenset(
    ALL_PERMISSIONS - {ADMIN_ACCESS, BILLING_ADMIN, BILLING_MANAGE}
)


def validate_custom_permissions(perms) -> tuple[bool, list[str]]:
    """Validate a caller-supplied custom permission override list against the allowlist."""
    if not isinstance(perms, list):
        return False, []
    cleaned: list[str] = []
    seen: set[str] = set()
    for p in perms:
        if not isinstance(p, str) or p not in ALLOWED_CUSTOM_PERMISSIONS:
            return False, []
        if p not in seen:
            seen.add(p)
            cleaned.append(p)
    return True, cleaned


def sanitize_custom_permissions(perms) -> list[str]:
    """Filter stored custom permission codes down to the explicit allowlist."""
    if not isinstance(perms, list):
        return []
    return [p for p in perms if isinstance(p, str) and p in ALLOWED_CUSTOM_PERMISSIONS]


def _resolve_team(service_or_project):
    """Resolve the Team from a Service, Project, or Deployment."""
    from django.db import models
    if not isinstance(service_or_project, models.Model):
        return None
    project = getattr(service_or_project, 'project', None)
    if project and isinstance(project, models.Model):
        team = getattr(project, 'team', None)
        if team and isinstance(team, models.Model):
            return team
    if hasattr(service_or_project, 'team'):
        team = service_or_project.team
        if isinstance(team, models.Model):
            return team
    # Walk deployment → service → project → team
    service = getattr(service_or_project, 'service', None)
    if service and isinstance(service, models.Model):
        return _resolve_team(service)
    return None


def _resolve_project(service_or_project):
    """Resolve the Project from a Service, Project, or Deployment."""
    from django.db import models
    if not isinstance(service_or_project, models.Model):
        return None
    project = getattr(service_or_project, 'project', None)
    if project and isinstance(project, models.Model):
        return project
    if hasattr(service_or_project, 'pk') and service_or_project.__class__.__name__ == 'Project':
        return service_or_project
    service = getattr(service_or_project, 'service', None)
    if service and isinstance(service, models.Model):
        proj = getattr(service, 'project', None)
        return proj if isinstance(proj, models.Model) else None
    return None


def get_permissions_for_role(role: str) -> list[str]:
    """Return the default permission codes for a team role.

    Args:
        role: One of ``ADMIN``, ``MEMBER``, ``VIEWER``.

    Returns:
        A list of permission code strings. Returns an empty list for
        unrecognized roles.
    """
    return list(DEFAULT_ROLE_PERMISSIONS.get(role, []))


def get_effective_role(user, service_or_project) -> str | None:
    """Return the effective role for a user on a resource.

    Checks project-level membership first (takes precedence), then falls
    back to team-level membership.

    Returns ``None`` if the user has no access at any level.
    """
    if not user or not user.is_authenticated:
        return None

    project = _resolve_project(service_or_project)

    # Project-level membership takes precedence.
    if project:
        try:
            from apps.organizations.models.project import ProjectMember
            pm = ProjectMember.objects.filter(
                project=project, user=user,
            ).first()
            if pm:
                if not getattr(pm, 'is_active', True):
                    return None
                if pm.expires_at and pm.expires_at < timezone.now():
                    return None
                return pm.role
        except (ImportError, Exception):
            pass

    # Fall back to team membership.
    team = _resolve_team(service_or_project)
    if team:
        try:
            from apps.teams.models import TeamMember
            membership = TeamMember.objects.filter(
                team=team, user=user,
            ).first()
            if membership:
                if not membership.is_active:
                    return None
                if membership.expires_at and membership.expires_at < timezone.now():
                    return None
                return membership.role
        except (ImportError, Exception):
            pass

    return None


def _get_team_member_permissions(user, team) -> list[str] | None:
    """Return custom permission overrides from TeamMember, or None if not set."""
    try:
        from apps.teams.models import TeamMember
        membership = TeamMember.objects.filter(
            team=team, user=user, is_active=True,
        ).exclude(
            expires_at__isnull=False, expires_at__lt=timezone.now(),
        ).first()
        if membership and isinstance(membership.permissions, list) and len(membership.permissions) > 0:
            return sanitize_custom_permissions(membership.permissions)
    except (ImportError, Exception):
        pass
    return None


def _get_project_member_permissions(user, project) -> list[str] | None:
    """Return custom permission overrides from ProjectMember, or None if not set."""
    try:
        from apps.organizations.models.project import ProjectMember
        pm = ProjectMember.objects.filter(project=project, user=user).first()
        if pm and not getattr(pm, 'is_active', True):
            return None
        if pm and pm.expires_at and pm.expires_at < timezone.now():
            return None
        if pm and isinstance(pm.permissions, list) and len(pm.permissions) > 0:
            return sanitize_custom_permissions(pm.permissions)
    except (ImportError, Exception):
        pass
    return None


def _is_org_owner(user, service_or_project) -> bool:
    """Check if the user is an org OWNER for the resource's organization."""
    project = _resolve_project(service_or_project)
    if not project:
        return False
    team = getattr(project, 'team', None)
    if not team:
        return False
    org = getattr(team, 'organization', None)
    if not org:
        return False
    if getattr(org, 'owner_id', None) == getattr(user, 'id', None):
        return True
    try:
        from apps.organizations.models import OrganizationMembership
        return (
            OrganizationMembership.objects.filter(
                organization=org, user=user, role='OWNER', is_active=True,
            )
            .exclude(expires_at__isnull=False, expires_at__lt=timezone.now())
            .exists()
        )
    except (ImportError, Exception):
        return False


def _has_billing_flag(user, team, org) -> bool:
    """Check billing-management flags on active memberships."""
    # Org OWNER or org member with explicit billing permission.
    try:
        from apps.organizations.models import OrganizationMembership
        if org:
            if getattr(org, 'owner_id', None) == getattr(user, 'id', None):
                return True
            om = (
                OrganizationMembership.objects.filter(
                    organization=org, user=user, is_active=True,
                )
                .exclude(expires_at__isnull=False, expires_at__lt=timezone.now())
                .first()
            )
            if om and (om.role == 'OWNER' or om.can_manage_billing):
                return True
    except (ImportError, Exception):
        pass

    # Active, unexpired team member with admin/billing flag.
    try:
        from apps.teams.models import TeamMember
        if team:
            tm = TeamMember.objects.filter(
                team=team, user=user, is_active=True,
            ).exclude(
                expires_at__isnull=False, expires_at__lt=timezone.now(),
            ).first()
            if tm and tm.can_manage_billing:
                return True
    except (ImportError, Exception):
        pass

    return False


def _get_user_permissions(user, obj=None) -> set[str]:
    """Return project-scoped permission codes for *user* relative to *obj*."""
    project = _resolve_project(obj) if obj is not None else None
    if project is None:
        return set()
    return set(get_user_permissions(user, project))


def user_can_manage_billing(user) -> bool:
    """Determine account-level billing management permission for *user*."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    has_any_org_or_team = False
    try:
        from apps.organizations.models import Organization, OrganizationMembership
        if Organization.objects.filter(owner=user).exists():
            return True
        now = timezone.now()
        active_orgs = OrganizationMembership.objects.filter(
            user=user, is_active=True,
        ).exclude(
            expires_at__isnull=False, expires_at__lt=now,
        )
        if active_orgs.filter(Q(role='OWNER') | Q(can_manage_billing=True)).exists():
            return True
        if active_orgs.filter(role__in=['ADMIN', 'MEMBER']).exists():
            has_any_org_or_team = True
    except (ImportError, Exception):
        pass

    try:
        from apps.teams.models import Team, TeamMember
        if Team.objects.filter(owner=user).exists():
            return True
        now = timezone.now()
        active_tm = TeamMember.objects.filter(
            user=user, is_active=True,
        ).exclude(
            expires_at__isnull=False, expires_at__lt=now,
        )
        if active_tm.filter(Q(role='ADMIN') | Q(can_manage_billing=True)).exists():
            return True
        # Only non-VIEWER active memberships put an account under managed billing;
        # dummy/VIEWER external invites must not lock out personal account billing.
        if active_tm.filter(role='MEMBER').exists():
            has_any_org_or_team = True
    except (ImportError, Exception):
        pass

    # Standalone personal accounts (not scoped to any team/org) manage their own billing.
    return not has_any_org_or_team


def user_can_view_billing(user) -> bool:
    """Determine account-level billing view permission for *user*."""
    if user_can_manage_billing(user):
        return True
    if not user or not user.is_authenticated:
        return False

    try:
        from apps.organizations.models import OrganizationMembership
        if (
            OrganizationMembership.objects.filter(
                user=user, is_active=True, role__in=['OWNER', 'ADMIN'],
            )
            .exclude(expires_at__isnull=False, expires_at__lt=timezone.now())
            .exists()
        ):
            return True
    except (ImportError, Exception):
        pass

    try:
        from apps.teams.models import TeamMember
        if TeamMember.objects.filter(
            user=user, is_active=True, role__in=['ADMIN', 'MEMBER'],
        ).exclude(
            expires_at__isnull=False, expires_at__lt=timezone.now(),
        ).exists():
            return True
    except (ImportError, Exception):
        pass

    return False


def has_permission(user, obj, code: str) -> bool:
    """Check whether *user* has *code* permission on *obj*.

    Resolution order (first match wins):
    1. Superuser → always True.
    2. Resource owner → always True.
    3. Org OWNER → always True (owner of the owning org).
    4. Billing-specific check via membership flags.
    5. ABAC: resource-level locks / restrict_to_creator / allowed_actions.
    6. Project-level role → permissions (with custom overrides).
    7. Team-level role → permissions (with custom overrides).

    Returns ``False`` when the user is not authenticated, the code is
    unrecognized, or no access path grants the permission.
    """
    if not user or not user.is_authenticated:
        return False

    if code not in ALL_PERMISSIONS:
        return False

    # Superuser sees everything.
    if user.is_superuser:
        return True

    # Resource owner sees everything on their own resources.
    if getattr(obj, 'owner', None) == user:
        return True

    team = _resolve_team(obj)
    project = _resolve_project(obj)
    org = team.organization if team and hasattr(team, 'organization') else None

    # Org OWNER sees everything within their org.
    if _is_org_owner(user, obj):
        return True

    # ── Billing-specific gates ──────────────────────────────────────
    if code in ('billing.manage', 'billing.admin'):
        if _has_billing_flag(user, team, org):
            return True
        return False
    if code == 'billing.view':
        if _has_billing_flag(user, team, org):
            return True

    # ── Project-level permissions ───────────────────────────────────
    if project:
        pm_perms = _get_project_member_permissions(user, project)
        if pm_perms is not None:
            # Project member override set explicitly — use it directly.
            return code in pm_perms

        from django.utils import timezone as _tz
        from apps.organizations.models.project import ProjectMember
        has_pm = ProjectMember.objects.filter(
            project=project, user=user,
        ).exclude(
            expires_at__isnull=False, expires_at__lt=_tz.now(),
        ).exists()
        if has_pm:
            # Project member with no custom override: use project role defaults.
            project_role = get_effective_role(user, obj)
            if project_role:
                defaults = get_permissions_for_role(project_role)
                return code in defaults

    # ── Team-level permissions ──────────────────────────────────────
    if team:
        tm_perms = _get_team_member_permissions(user, team)
        if tm_perms is not None:
            return code in tm_perms

    role = get_effective_role(user, obj)
    if role:
        defaults = get_permissions_for_role(role)
        return code in defaults

    if obj is not None and code in _get_user_permissions(user, obj):
        return True

    return False


def get_user_permissions(user, project=None) -> list[str]:
    """Return all permission codes *user* has.

    When *project* is provided, permissions are scoped to that project
    (team role + project role merged). Otherwise returns global roles.
    """
    if not user or not user.is_authenticated:
        return []
    if user.is_superuser:
        return sorted(ALL_PERMISSIONS)

    permissions: set[str] = set()

    if project:
        team = getattr(project, 'team', None)
        if team:
            tm_perms = _get_team_member_permissions(user, team)
            if tm_perms is not None:
                permissions.update(tm_perms)
            else:
                role = get_effective_role(user, project)
                if role:
                    permissions.update(get_permissions_for_role(role))

        pm_perms = _get_project_member_permissions(user, project)
        if pm_perms is not None:
            permissions.update(pm_perms)  # Project overrides/augments
        else:
            # If project member without custom perms, still resolve role
            try:
                from apps.organizations.models.project import ProjectMember
                pm = ProjectMember.objects.filter(
                    project=project, user=user,
                ).exclude(
                    expires_at__isnull=False, expires_at__lt=timezone.now(),
                ).first()
                if pm:
                    permissions.update(get_permissions_for_role(pm.role))
            except (ImportError, Exception):
                pass
    else:
        # Global: return union of all team roles.
        try:
            from apps.teams.models import TeamMember
            memberships = TeamMember.objects.filter(
                user=user, is_active=True,
            ).exclude(
                expires_at__isnull=False, expires_at__lt=timezone.now(),
            )
            for m in memberships:
                if isinstance(m.permissions, list) and len(m.permissions) > 0:
                    permissions.update(sanitize_custom_permissions(m.permissions))
                else:
                    permissions.update(get_permissions_for_role(m.role))
        except (ImportError, Exception):
            pass

    return sorted(permissions)


def get_accessible_q(user) -> Q:
    """Return a Django Q filter for queryset scoping.

    Returns a filter matching resources owned by *user*, belonging to a
    team where *user* is a member, or belonging to a project where *user*
    is a project-level member. Superusers see all resources.
    """
    if user.is_superuser:
        return Q()

    q = Q(owner=user)

    try:
        from apps.teams.models import TeamMember
        team_ids = list(
            TeamMember.objects.filter(
                user=user, is_active=True,
            ).exclude(
                expires_at__isnull=False, expires_at__lt=timezone.now(),
            ).values_list('team_id', flat=True)
        )
        if team_ids:
            q |= Q(project__team_id__in=team_ids)
    except (ImportError, Exception):
        pass

    try:
        from apps.organizations.models.project import ProjectMember
        project_ids = list(
            ProjectMember.objects.filter(user=user).exclude(
                expires_at__isnull=False, expires_at__lt=timezone.now(),
            ).values_list('project_id', flat=True)
        )
        if project_ids:
            q |= Q(project_id__in=project_ids)
    except (ImportError, Exception):
        pass

    return q
