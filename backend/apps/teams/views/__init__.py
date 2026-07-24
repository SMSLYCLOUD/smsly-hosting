"""Views module."""
from __future__ import annotations

import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import Team, TeamMember
from ..serializers import InviteMemberSerializer, TeamMemberSerializer, TeamSerializer

logger = logging.getLogger(__name__)


def _is_team_admin(team, user) -> bool:
    return TeamMember.objects.filter(
        team=team, user=user, role=TeamMember.Role.ADMIN, is_active=True,
    ).exists()


class TeamViewSet(viewsets.ModelViewSet):
    queryset = Team.objects.all().order_by('name')
    serializer_class = TeamSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        return self.queryset.filter(Q(owner=self.request.user) | Q(
            members__user=self.request.user)).distinct().order_by('name')

    def perform_create(self, serializer):
        team = serializer.save(owner=self.request.user)
        TeamMember.objects.create(
            team=team,
            user=self.request.user,
            role=TeamMember.Role.ADMIN,
        )

    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        team = self.get_object()
        members = team.members.all().order_by('role', 'user__username')
        serializer = TeamMemberSerializer(members, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], serializer_class=InviteMemberSerializer)
    def invite_member(self, request, pk=None):
        logger.info("invite_member called: user=%s team_pk=%s", request.user, pk)
        team = self.get_object()
        if not _is_team_admin(team, request.user):
            return Response(
                {'error': 'Only team admins can invite members'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            role = serializer.validated_data['role']
            expires_at = serializer.validated_data.get('expires_at')
            can_manage_billing = serializer.validated_data.get('can_manage_billing', False)

            try:
                user_obj = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

            if TeamMember.objects.filter(team=team, user=user_obj).exists():
                return Response({'error': 'User already in team'}, status=status.HTTP_400_BAD_REQUEST)

            TeamMember.objects.create(
                team=team, user=user_obj, role=role,
                expires_at=expires_at, can_manage_billing=can_manage_billing,
            )

            try:
                send_mail(
                    subject=f"You've been invited to join {team.name} on SMSLY",
                    message=f"Hello {user_obj.username},\n\nYou have been invited to join the team "
                            f"'{team.name}' as a {role}.\n\nLog in to accept: "
                            f"{getattr(settings, 'SITE_URL', 'http://localhost:3000')}",
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smsly.com'),
                    recipient_list=[email],
                    fail_silently=False,
                )
            except SMTPException as mail_exc:
                logger.error("invite_member: SMTP failure for team=%s invitee=%s: %s", team.id, email, mail_exc)
                return Response({"error": "smtp_failure", "mail_error": str(mail_exc)}, status=status.HTTP_502_BAD_GATEWAY)
            except (OSError, ConnectionError, TimeoutError) as mail_exc:
                logger.error("invite_member: mail transport failure for team=%s invitee=%s: %s", team.id, email, mail_exc)
                return Response({"error": "smtp_failure", "mail_error": str(mail_exc)}, status=status.HTTP_502_BAD_GATEWAY)

            return Response({'status': 'invited'})
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        team = self.get_object()
        if not _is_team_admin(team, request.user):
            return Response({'error': 'Only team admins can remove members'}, status=status.HTTP_403_FORBIDDEN)
        user_id = request.data.get('user_id')
        try:
            member = TeamMember.objects.get(team=team, user__id=user_id)
            if member.role == TeamMember.Role.ADMIN and team.members.filter(
                role=TeamMember.Role.ADMIN, is_active=True,
            ).count() == 1:
                return Response({'error': 'Cannot remove last admin'}, status=status.HTTP_400_BAD_REQUEST)
            member.delete()
            return Response({'status': 'removed'})
        except TeamMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def change_role(self, request, pk=None):
        """Change a member's role, expiration, or billing flag."""
        team = self.get_object()
        if not _is_team_admin(team, request.user):
            return Response({'error': 'Only team admins can change roles'}, status=status.HTTP_403_FORBIDDEN)
        user_id = request.data.get('user_id')
        new_role = request.data.get('role')
        expires_at = request.data.get('expires_at')
        can_manage_billing = request.data.get('can_manage_billing')
        permissions_override = request.data.get('permissions')

        try:
            member = TeamMember.objects.get(team=team, user__id=user_id)
        except TeamMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

        if new_role and new_role in dict(TeamMember.Role.choices):
            member.role = new_role
        if expires_at is not None:
            member.expires_at = expires_at if expires_at else None
        if can_manage_billing is not None:
            member.can_manage_billing = bool(can_manage_billing)
        if permissions_override is not None:
            member.permissions = permissions_override if isinstance(permissions_override, list) else []

        member.save(update_fields=[
            f for f in ('role', 'expires_at', 'can_manage_billing', 'permissions')
            if f in request.data or request.data.get(f) is not None
        ] if any(k in request.data for k in ('role', 'expires_at', 'can_manage_billing', 'permissions')) else None)

        serializer = TeamMemberSerializer(member)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def suspend_member(self, request, pk=None):
        """Toggle a member's active status (suspend/unsuspend)."""
        team = self.get_object()
        if not _is_team_admin(team, request.user):
            return Response({'error': 'Only team admins can suspend members'}, status=status.HTTP_403_FORBIDDEN)
        user_id = request.data.get('user_id')
        try:
            member = TeamMember.objects.get(team=team, user__id=user_id)
        except TeamMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

        member.is_active = not member.is_active
        member.save(update_fields=['is_active'])
        serializer = TeamMemberSerializer(member)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def toggle_billing(self, request, pk=None):
        """Toggle can_manage_billing flag on a member."""
        team = self.get_object()
        if not _is_team_admin(team, request.user):
            return Response({'error': 'Only team admins can manage billing access'}, status=status.HTTP_403_FORBIDDEN)
        user_id = request.data.get('user_id')
        try:
            member = TeamMember.objects.get(team=team, user__id=user_id)
        except TeamMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

        member.can_manage_billing = not member.can_manage_billing
        member.save(update_fields=['can_manage_billing'])
        serializer = TeamMemberSerializer(member)
        return Response(serializer.data)
