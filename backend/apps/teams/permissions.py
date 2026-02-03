"""Permissions module."""
from rest_framework import permissions
from .models import TeamMember


class IsTeamMember(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        # Assuming obj has a 'team' attribute or is a Team
        team = getattr(obj, 'team', obj)
        return TeamMember.objects.filter(team=team, user=request.user).exists()
