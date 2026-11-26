"""Views module."""
from __future__ import annotations

import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.permissions.utils import validate_custom_permissions

from ..models import Team, TeamMember
from ..serializers import InviteMemberSerializer, TeamMemberSerializer, TeamSerializer

logger = logging.getLogger(__name__)


def _dispatch_team_invite_email(team_id, team_name: str, username: str, email: str, role: str) -> None:
    """Send team invitation email after DB commit, never leaking SMTP errors."""
    try:
        send_mail(
            subject=f"You've been invited to join {team_name} on SMSLY",
            message=f"Hello {username},\n\nYou have been invited to join the team "
                    f"'{team_name}' as a {role}.\n\nLog in to accept: "
                    f"{getattr(settings, 'SITE_URL', 'http://localhost:3000')}",
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smsly.com'),
            recipient_list=[email],
            fail_silently=False,
        )
    except SMTPException as mail_exc:
        logger.error("invite_member: SMTP failure for team=%s invitee=%s: %s", team_id, email, mail_exc)
    except (OSError, ConnectionError, TimeoutError, Exception) as mail_exc:
        logger.error("invite_member: mail transport failure for team=%s invitee=%s: %s", team_id, email, mail_exc)


def _is_team_admin(team, user) -> bool:
    from django.db.models import Q
    from django.utils import timezone
    return TeamMember.objects.filter(
        team=team, user=user, role=TeamMember.Role.ADMIN, is_active=True,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    ).exists()


class TeamViewSet(viewsets.ModelViewSet):
    queryset = Team.objects.all().order_by('name')
    serializer_class = TeamSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        from django.utils import timezone
        now = timezone.now()
        return self.queryset.filter(
            Q(owner=self.request.user)
            | (
                Q(members__user=self.request.user, members__is_active=True)
                & (Q(members__expires_at__isnull=True) | Q(members__expires_at__gt=now))
            )
        ).distinct().order_by('name')

    def perform_create(self, serializer):
        team = serializer.save(owner=self.request.user)
        TeamMember.objects.create(
            team=team,
            user=self.request.user,
            role=TeamMember.Role.ADMIN,
        )

    def perform_update(self, serializer):
        from rest_framework.exceptions import PermissionDenied
        team = self.get_object()
        user = self.request.user
        if not (user.is_superuser or team.owner_id == user.id or _is_team_admin(team, user)):
            raise PermissionDenied("Only team admins can update this team.")
        serializer.save()

    def perform_destroy(self, instance):
        from rest_framework.exceptions import PermissionDenied
        user = self.request.user
        if not (user.is_superuser or instance.owner_id == user.id or _is_team_admin(instance, user)):
            raise PermissionDenied("Only team admins can delete this team.")
        instance.delete()

    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        team = self.get_object()
        members = team.members.all().order_by('role', 'user__username')
        serializer = TeamMemberSerializer(members, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], serializer_class=InviteMemberSerializer, throttle_classes=[UserRateThrottle])
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
            permissions_override = request.data.get('permissions')
            cleaned_perms: list[str] = []
            if permissions_override is not None:
                ok_perms, cleaned_perms = validate_custom_permissions(permissions_override)
                if not ok_perms:
                    return Response(
                        {'error': 'Invalid or disallowed permission override codes.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            try:
                user_obj = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

            with transaction.atomic():
                if TeamMember.objects.select_for_update().filter(team=team, user=user_obj).exists():
                    return Response({'error': 'User already in team'}, status=status.HTTP_400_BAD_REQUEST)

                TeamMember.objects.create(
                    team=team, user=user_obj, role=role,
                    expires_at=expires_at, can_manage_billing=can_manage_billing,
                    permissions=cleaned_perms,
                )
                team_id = team.id
                team_name = team.name
                username = user_obj.username
                transaction.on_commit(
                    lambda: _dispatch_team_invite_email(team_id, team_name, username, email, role)
                )

            return Response({'status': 'invited'})
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        from django.db import transaction
        from django.db.models import Q
        from django.utils import timezone

        team = self.get_object()
        if not _is_team_admin(team, request.user):
            return Response({'error': 'Only team admins can remove members'}, status=status.HTTP_403_FORBIDDEN)
        user_id = request.data.get('user_id')
        now = timezone.now()
        with transaction.atomic():
            try:
                member = TeamMember.objects.select_for_update().get(team=team, user__id=user_id)
            except TeamMember.DoesNotExist:
                return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)
            if member.role == TeamMember.Role.ADMIN and member.is_active and (
                member.expires_at is None or member.expires_at > now
            ):
                active_admins = (
                    TeamMember.objects.select_for_update()
                    .filter(team=team, role=TeamMember.Role.ADMIN, is_active=True)
                    .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                    .count()
                )
                if active_admins <= 1:
                    return Response({'error': 'Cannot remove last admin'}, status=status.HTTP_400_BAD_REQUEST)
            member.delete()
        return Response({'status': 'removed'})

    @action(detail=True, methods=['post'])
    def change_role(self, request, pk=None):
        """Change a member's role, expiration, or billing flag."""
        from django.db.models import Q
        from django.utils import timezone

        team = self.get_object()
        if not _is_team_admin(team, request.user):
            return Response({'error': 'Only team admins can change roles'}, status=status.HTTP_403_FORBIDDEN)
        user_id = request.data.get('user_id')
        new_role = request.data.get('role')
        expires_at = request.data.get('expires_at')
        can_manage_billing = request.data.get('can_manage_billing')
        permissions_override = request.data.get('permissions')

        if new_role is not None and new_role not in dict(TeamMember.Role.choices):
            return Response({'error': 'Invalid role'}, status=status.HTTP_400_BAD_REQUEST)

        cleaned_perms = None
        if permissions_override is not None:
            ok_perms, cleaned_perms = validate_custom_permissions(permissions_override)
            if not ok_perms:
                return Response(
                    {'error': 'Invalid or disallowed permission override codes.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        now = timezone.now()
        with transaction.atomic():
            try:
                member = TeamMember.objects.select_for_update().get(team=team, user__id=user_id)
            except TeamMember.DoesNotExist:
                return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

            was_active_admin = (
                member.role == TeamMember.Role.ADMIN
                and member.is_active
                and (member.expires_at is None or member.expires_at > now)
            )
            if was_active_admin and new_role and new_role != TeamMember.Role.ADMIN:
                active_admins = (
                    TeamMember.objects.select_for_update()
                    .filter(team=team, role=TeamMember.Role.ADMIN, is_active=True)
                    .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                    .count()
                )
                if active_admins <= 1:
                    return Response({'error': 'Cannot demote last admin'}, status=status.HTTP_400_BAD_REQUEST)

            if new_role:
                member.role = new_role
            if expires_at is not None:
                member.expires_at = expires_at if expires_at else None
            if can_manage_billing is not None:
                member.can_manage_billing = bool(can_manage_billing)
            if permissions_override is not None:
                member.permissions = cleaned_perms

            member.save(update_fields=[
                f for f in ('role', 'expires_at', 'can_manage_billing', 'permissions')
                if f in request.data or request.data.get(f) is not None
            ] if any(k in request.data for k in ('role', 'expires_at', 'can_manage_billing', 'permissions')) else None)

        serializer = TeamMemberSerializer(member)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def suspend_member(self, request, pk=None):
        """Toggle a member's active status (suspend/unsuspend)."""
        from django.db import transaction
        from django.db.models import Q
        from django.utils import timezone

        team = self.get_object()
        if not _is_team_admin(team, request.user):
            return Response({'error': 'Only team admins can suspend members'}, status=status.HTTP_403_FORBIDDEN)
        user_id = request.data.get('user_id')
        now = timezone.now()
        with transaction.atomic():
            try:
                member = TeamMember.objects.select_for_update().get(team=team, user__id=user_id)
            except TeamMember.DoesNotExist:
                return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

            if member.is_active and member.role == TeamMember.Role.ADMIN and (
                member.expires_at is None or member.expires_at > now
            ):
                active_admins = (
                    TeamMember.objects.select_for_update()
                    .filter(team=team, role=TeamMember.Role.ADMIN, is_active=True)
                    .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                    .count()
                )
                if active_admins <= 1:
                    return Response({'error': 'Cannot suspend last admin'}, status=status.HTTP_400_BAD_REQUEST)

            member.is_active = not member.is_active
            member.save(update_fields=['is_active'])
        serializer = TeamMemberSerializer(member)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def leave(self, request, pk=None):
        """Allow an authenticated member to leave a team (unless last admin)."""
        from django.db import transaction
        from django.db.models import Q
        from django.utils import timezone

        team = self.get_object()
        now = timezone.now()
        with transaction.atomic():
            member = TeamMember.objects.select_for_update().filter(team=team, user=request.user).first()
            if not member:
                return Response({'error': 'You are not a member of this team.'}, status=status.HTTP_404_NOT_FOUND)
            if member.role == TeamMember.Role.ADMIN and member.is_active and (
                member.expires_at is None or member.expires_at > now
            ):
                active_admins = (
                    TeamMember.objects.select_for_update()
                    .filter(team=team, role=TeamMember.Role.ADMIN, is_active=True)
                    .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                    .count()
                )
                if active_admins <= 1:
                    return Response({'error': 'Cannot leave as the last admin'}, status=status.HTTP_400_BAD_REQUEST)
            member.delete()
        return Response({'status': 'left'})

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
