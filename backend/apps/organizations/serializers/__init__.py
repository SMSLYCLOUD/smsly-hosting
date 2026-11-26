"""Organization serializers."""
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers

from ..models import Organization, OrganizationMembership, OrganizationSSO


def _active_unexpired_membership_q():
    now = timezone.now()
    return Q(is_active=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=now))


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
        return obj.memberships.filter(_active_unexpired_membership_q()).count()

    def get_role(self, obj):
        roles = getattr(self, '_user_roles', None)
        if roles is not None:
            return roles.get(obj.id)
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return None
        if getattr(obj, 'owner_id', None) == getattr(user, 'id', None):
            return OrganizationMembership.Role.OWNER
        membership = (
            OrganizationMembership.objects.filter(user=user, organization=obj)
            .filter(_active_unexpired_membership_q())
            .first()
        )
        return membership.role if membership else None

    @classmethod
    def prefetch_for_list(cls, items, user, context=None):
        """Construct a ``many=True`` serializer with bulk-fetched counts and roles."""
        items_list = list(items)
        org_ids = [obj.id for obj in items_list]
        active_q = _active_unexpired_membership_q()
        if org_ids:
            member_counts = dict(
                OrganizationMembership.objects.filter(
                    organization_id__in=org_ids,
                )
                .filter(active_q)
                .values('organization_id')
                .annotate(cnt=Count('id'))
                .values_list('organization_id', 'cnt')
            )
            if user and getattr(user, 'is_authenticated', False):
                user_roles = dict(
                    OrganizationMembership.objects.filter(
                        user=user, organization_id__in=org_ids,
                    )
                    .filter(active_q)
                    .values_list('organization_id', 'role')
                )
                user_id = getattr(user, 'id', None)
                for obj in items_list:
                    if getattr(obj, 'owner_id', None) == user_id:
                        user_roles.setdefault(obj.id, OrganizationMembership.Role.OWNER)
            else:
                user_roles = {}
        else:
            member_counts = {}
            user_roles = {}

        serializer = cls(items_list, many=True, context=context or {})
        serializer.child._member_counts = member_counts
        serializer.child._user_roles = user_roles
        return serializer

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
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'oidc_client_secret': {'write_only': True},
            'saml_x509_cert': {'write_only': True},
        }

    def validate_default_role(self, value):
        if str(value or '').upper() == 'OWNER':
            raise serializers.ValidationError("default_role cannot be OWNER.")
        return value

    def update(self, instance, validated_data):
        # Prevent moving an existing SSO config across organizations on PATCH/PUT.
        validated_data.pop('organization', None)
        return super().update(instance, validated_data)
