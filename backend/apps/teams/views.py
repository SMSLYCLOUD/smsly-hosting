"""Views module."""
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth.models import User
from .models import Team, TeamMember
from .serializers import TeamSerializer, InviteMemberSerializer
from django.core.mail import send_mail
from django.conf import settings


class TeamSerializer(serializers.ModelSerializer):
    members_count = serializers.IntegerField(
        source='members.count', read_only=True)

    class Meta:
        model = Team
        fields = ['id', 'name', 'created_at', 'members_count']


class TeamViewSet(viewsets.ModelViewSet):
    serializer_class = TeamSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Return teams owned by user or where user is a member
        from django.db.models import Q
        return Team.objects.filter(Q(owner=self.request.user) | Q(
            members__user=self.request.user)).distinct()

    def perform_create(self, serializer):
        team = serializer.save(owner=self.request.user)
        # Add creator as admin
        TeamMember.objects.create(
            user=self.request.user,
            role=TeamMember.Role.ADMIN)

    @action(detail=True, methods=['post'], serializer_class=InviteMemberSerializer)
    def invite_member(self, request, pk=None):
        team = self.get_object()
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            role = serializer.validated_data['role']
            
            # Find user by email
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

            # Check if already member
            if TeamMember.objects.filter(team=team, user=user).exists():
                return Response({'error': 'User already in team'}, status=status.HTTP_400_BAD_REQUEST)

            TeamMember.objects.create(team=team, user=user, role=role)
            
            # Send Notification
            try:
                send_mail(
                    subject=f"You've been invited to join {team.name} on SMSLY",
                    message=f"Hello {user.username},\n\nYou have been invited to join the team '{team.name}' as a {role}.\n\nLog in to accept: {getattr(settings, 'SITE_URL', 'http://localhost:3000')}",
                    from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smsly.com'),
                    recipient_list=[email],
                    fail_silently=True
                )
            except Exception:
                pass # Non-blocking

            return Response({'status': 'invited'})
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        team = self.get_object()
        user_id = request.data.get('user_id')
        
        try:
            member = TeamMember.objects.get(team=team, user__id=user_id)
            if member.role == TeamMember.Role.ADMIN and team.members.filter(role=TeamMember.Role.ADMIN).count() == 1:
                 return Response({'error': 'Cannot remove last admin'}, status=status.HTTP_400_BAD_REQUEST)
            
            member.delete()
            return Response({'status': 'removed'})
        except TeamMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)
