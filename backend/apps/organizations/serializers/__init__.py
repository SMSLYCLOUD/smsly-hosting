"""Organization serializers."""
from django.db.models import Count, Q
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
        counts = getattr(self, '_member_counts', None)
        if counts is not None:
            return counts.get(obj.id, 0)
        return obj.memberships.count()

    def get_role(self, obj):
        roles = getattr(self, '_user_roles', None)
        if roles is not None:
            return roles.get(obj.id)
        user = self.context['request'].user
        membership = OrganizationMembership.objects.filter(user=user, organization=obj).first()
        return membership.role if membership else None

    @classmethod
    def prefetch_for_list(cls, queryset, user):
        """Attach bulk-fetched data to avoid N+1 in list views."""
        org_ids = list(queryset.values_list('id', flat=True))
        member_counts = dict(
            OrganizationMembership.objects.filter(
                organization_id__in=org_ids,
            ).values('organization_id').annotate(cnt=Count('id')).values_list('organization_id', 'cnt')
        )
        user_roles = dict(
            OrganizationMembership.objects.filter(
                user=user, organization_id__in=org_ids,
            ).values_list('organization_id', 'role')
        )
        instance = cls()
        instance._member_counts = member_counts
        instance._user_roles = user_roles
        return instance

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
        fields = [
            'id', 'organization', 'provider_type', 'label', 'is_active',
            'oidc_issuer_url', 'oidc_client_id', 'oidc_client_secret',
            'saml_entity_id', 'saml_sso_url', 'saml_x509_cert',
            'auto_provision_domains', 'default_role',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'organization']
        extra_kwargs = {
            'oidc_client_secret': {'write_only': True},
            'saml_x509_cert': {'write_only': True},
        }
