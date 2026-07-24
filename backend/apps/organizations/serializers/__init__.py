"""Organization serializers."""
from rest_framework import serializers

from ..models import Organization, OrganizationMembership, OrganizationSSO


class OrganizationSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ['id', 'name', 'slug', 'owner', 'member_count', 'role', 'created_at', 'updated_at']
        read_only_fields = ['id', 'owner', 'created_at', 'updated_at']

    def get_member_count(self, obj):
        return obj.memberships.count()

    def get_role(self, obj):
        user = self.context['request'].user
        membership = OrganizationMembership.objects.filter(user=user, organization=obj).first()
        return membership.role if membership else None

    def create(self, validated_data):
        user = self.context['request'].user
        org = Organization.objects.create(owner=user, **validated_data)
        OrganizationMembership.objects.create(
            organization=org, user=user, role=OrganizationMembership.Role.OWNER,
        )
        return org


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = ['id', 'user', 'username', 'email', 'role', 'invited_at', 'accepted_at']
        read_only_fields = ['id', 'user', 'invited_at', 'accepted_at']


class InviteMemberSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=[r.value for r in OrganizationMembership.Role if r.value != 'OWNER'],  # type: ignore[attr-defined]  # TextChoices is iterable at runtime; mypy stubs only model the enum-style access.
        default=OrganizationMembership.Role.MEMBER,
    )


class OrganizationSSOSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationSSO
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'organization']
        extra_kwargs = {
            'oidc_client_secret': {'write_only': True},
            'saml_x509_cert': {'write_only': True},
        }
