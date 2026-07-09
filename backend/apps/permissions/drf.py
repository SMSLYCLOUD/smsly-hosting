"""DRF permission classes for fine-grained RBAC.

These classes integrate with ``apps.permissions.utils`` to provide
declarative, auditable permission checks on every view/viewset.
"""

from __future__ import annotations

import logging

from rest_framework import permissions

from .codes import ADMIN_ACCESS, BILLING_MANAGE, BILLING_VIEW
from .utils import has_permission

logger = logging.getLogger(__name__)


class HasPermission(permissions.BasePermission):
    """Grant access only if the user has a specific permission code.

    Usage::

        class MyViewSet(ModelViewSet):
            permission_classes = [IsAuthenticated, HasPermission('service.create')]
    """

    def __init__(self, code: str):
        self.code = code

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        # For list/create actions we don't have a specific object yet.
        # has_permission() requires an object, so for list we check
        # against the view's queryset model.
        getattr(view, 'swagger_fake_view', None) is not None
        if view.action in ('list', 'create', 'metadata'):
            # Use queryset model as a proxy — the object check happens
            # in has_object_permission() below.
            queryset = view.get_queryset() if hasattr(view, 'get_queryset') else view.queryset
            if hasattr(queryset, 'model'):
                result = has_permission(request.user, queryset.model(), self.code)
                if not result:
                    request._denied_permission = self.code  # type: ignore[attr-defined]
                return result
            return True
        return True

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        result = has_permission(request.user, obj, self.code)
        if not result:
            request._denied_permission = self.code  # type: ignore[attr-defined]
            logger.debug(
                "HasPermission denied: user=%s code=%s obj=%s",
                request.user.id, self.code, getattr(obj, 'pk', '?'),
            )
        return result


class HasAnyPermission(permissions.BasePermission):
    """Grant access if the user has ANY of the given permission codes."""

    def __init__(self, *codes: str):
        self.codes = codes

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        for code in self.codes:
            if has_permission(request.user, obj, code):
                return True
        request._denied_permission = self.codes[0] if self.codes else 'unknown'  # type: ignore[attr-defined]
        return False


class HasAllPermissions(permissions.BasePermission):
    """Grant access only if the user has ALL of the given permission codes."""

    def __init__(self, *codes: str):
        self.codes = codes

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        for code in self.codes:
            if not has_permission(request.user, obj, code):
                request._denied_permission = code  # type: ignore[attr-defined]
                return False
        return True


class IsTeamAdmin(permissions.BasePermission):
    """Grant access if the user is a team ADMIN on the resource's team."""

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        from apps.teams.permissions import user_is_team_admin
        result = user_is_team_admin(request.user, obj)
        if not result:
            request._denied_permission = 'team.admin'  # type: ignore[attr-defined]
        return result


class IsOrgAdmin(permissions.BasePermission):
    """Grant access if the user is an org ADMIN or OWNER."""

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        from apps.organizations.permissions import get_org_from_resource, is_admin
        org = get_org_from_resource(obj)
        if not org:
            return False
        result = is_admin(request.user, org)
        if not result:
            request._denied_permission = 'org.admin'  # type: ignore[attr-defined]
        return result


class IsOrgOwner(permissions.BasePermission):
    """Grant access only if the user is an org OWNER."""

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        from apps.organizations.permissions import get_org_from_resource, is_owner
        org = get_org_from_resource(obj)
        if not org:
            return False
        result = is_owner(request.user, org)
        if not result:
            request._denied_permission = 'org.owner'  # type: ignore[attr-defined]
        return result


class CanManageBilling(permissions.BasePermission):
    """Grant access if the user can manage billing (plans, checkout, invoices)."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        # Billing management checks don't require a specific object.
        # Check against a dummy — has_permission resolves membership flags.
        result = has_permission(request.user, object(), BILLING_MANAGE)
        if not result:
            request._denied_permission = BILLING_MANAGE  # type: ignore[attr-defined]
        return result


class CanViewBilling(permissions.BasePermission):
    """Grant access if the user can view billing info."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        result = has_permission(request.user, object(), BILLING_VIEW)
        if not result:
            request._denied_permission = BILLING_VIEW  # type: ignore[attr-defined]
        return result


class HasAdminAccess(permissions.BasePermission):
    """Grant access if the user has admin.access permission (staff dashboard, etc.)."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        result = has_permission(request.user, object(), ADMIN_ACCESS)
        if not result:
            request._denied_permission = ADMIN_ACCESS  # type: ignore[attr-defined]
        return result


class ViewSetPermissionsMixin:
    """Mixin that applies different permission classes per action.

    Usage::

        class MyViewSet(ViewSetPermissionsMixin, viewsets.ModelViewSet):
            action_permissions = {
                'create': [HasPermission('service.create')],
                'destroy': [HasPermission('service.delete')],
                'deploy': [HasPermission('service.deploy')],
            }
            permission_classes = [permissions.IsAuthenticated]
    """

    action_permissions: dict[str, list] = {}

    def get_permissions(self):
        base = super().get_permissions()
        action = self.action
        extra = self.action_permissions.get(action, [])
        return base + [p() if callable(p) else p for p in extra]
