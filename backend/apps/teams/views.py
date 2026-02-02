from rest_framework import viewsets, permissions
from .models import Team, TeamMember
from rest_framework import serializers

class TeamSerializer(serializers.ModelSerializer):
    members_count = serializers.IntegerField(source='members.count', read_only=True)

    class Meta:
        model = Team
        fields = ['id', 'name', 'created_at', 'members_count']

class TeamViewSet(viewsets.ModelViewSet):
    serializer_class = TeamSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Return teams owned by user or where user is a member
        from django.db.models import Q
        return Team.objects.filter(Q(owner=self.request.user) | Q(members__user=self.request.user)).distinct()

    def perform_create(self, serializer):
        team = serializer.save(owner=self.request.user)
        # Add creator as admin
        TeamMember.objects.create(team=team, user=self.request.user, role=TeamMember.Role.ADMIN)
