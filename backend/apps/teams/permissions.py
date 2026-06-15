"""Permissions module."""
from rest_framework import permissions
from .models import TeamMember


class IsTeamMember(permissions.BasePermission):
    """Allows access if user is a member (any role) of the team."""
    def has_object_permission(self, request, view, obj):
        team = getattr(obj, 'team', obj)
        return TeamMember.objects.filter(team=team, user=request.user).exists()


class IsTeamAdmin(permissions.BasePermission):
    """Allows access only if user has ADMIN role on the team."""
    def has_object_permission(self, request, view, obj):
        team = getattr(obj, 'team', obj)
        return TeamMember.objects.filter(
            team=team, user=request.user, role=TeamMember.Role.ADMIN
        ).exists()
