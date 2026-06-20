from rest_framework import serializers

from .models import Team, TeamMember


class TeamSerializer(serializers.ModelSerializer):
    members_count = serializers.IntegerField(source='members.count', read_only=True)

    class Meta:
        model = Team
        fields = ('id', 'name', 'created_at', 'members_count', 'owner')
        read_only_fields = ('owner', 'created_at')

class TeamMemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = TeamMember
        fields = ('id', 'user', 'username', 'email', 'role', 'team')
        read_only_fields = ('user', 'team')

class InviteMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=TeamMember.Role.choices)
