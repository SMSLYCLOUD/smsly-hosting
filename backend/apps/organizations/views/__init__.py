"""Organization API views."""
import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from ..models import Organization, OrganizationMembership, OrganizationSSO
from ..permissions import assert_admin, assert_owner, get_org_q_filter
from ..serializers import (
    InviteMemberSerializer,
    OrganizationMembershipSerializer,
    OrganizationSerializer,
    OrganizationSSOSerializer,
)

logger = logging.getLogger(__name__)


def _dispatch_org_invite_email(org_id, org_name: str, username: str, email: str, role: str) -> None:
    """Send organization invite email post-commit; never surface SMTP details."""
    try:
        site_url = str(getattr(settings, 'SITE_URL', 'http://localhost:3000') or 'http://localhost:3000').rstrip('/')
        accept_url = f"{site_url}/settings/team?accept_org={org_id}"
        send_mail(
            subject=f"You've been invited to join {org_name} on SMSLY",
            message=(
                f"Hello {username},\n\n"
                f"You have been invited to join the organization '{org_name}' as {role}.\n\n"
                f"Log in to accept: {accept_url}"
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smsly.com'),
            recipient_list=[email],
            fail_silently=False,
        )
    except (SMTPException, OSError, ConnectionError, TimeoutError, Exception) as mail_exc:
        logger.error("org invite email failed for org=%s invitee=%s: %s", org_id, email, mail_exc)


class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(get_org_q_filter(self.request.user)).prefetch_related('memberships')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        items = page if page is not None else list(queryset)
        serializer = OrganizationSerializer.prefetch_for_list(
            items, request.user, context=self.get_serializer_context()
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def perform_update(self, serializer):
        assert_admin(self.request.user, serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        assert_owner(self.request.user, instance)
        instance.delete()

    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        org = self.get_object()
        now = timezone.now()
        members = (
            OrganizationMembership.objects.filter(
                organization=org,
                is_active=True,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .select_related('user')
        )
        return Response(OrganizationMembershipSerializer(members, many=True).data)

    @action(detail=True, methods=['post'], throttle_classes=[UserRateThrottle])
    def invite(self, request, pk=None):
        org = self.get_object()
        assert_admin(request.user, org)

        serializer = InviteMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email']
        role = serializer.validated_data['role']

        User = get_user_model()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response(
                {'error': 'Forbidden'},
                status=status.HTTP_403_FORBIDDEN,
            )

        with transaction.atomic():
            existing = (
                OrganizationMembership.objects.select_for_update()
                .filter(organization=org, user=user)
                .first()
            )
            if existing is not None:
                if existing.is_active:
                    return Response(
                        {'error': 'User is already a member or has a pending invitation.'},
                        status=status.HTTP_409_CONFLICT,
                    )
                existing.role = role
                existing.invited_at = timezone.now()
                existing.expires_at = None
                existing.save(update_fields=['role', 'invited_at', 'expires_at'])
                membership = existing
                resp_status = status.HTTP_200_OK
            else:
                membership = OrganizationMembership.objects.create(
                    organization=org,
                    user=user,
                    role=role,
                    is_active=False,
                )
                resp_status = status.HTTP_201_CREATED
            org_id = org.id
            org_name = org.name
            username = user.username
            transaction.on_commit(
                lambda: _dispatch_org_invite_email(org_id, org_name, username, email, role)
            )

        return Response(OrganizationMembershipSerializer(membership).data, status=resp_status)

    @action(detail=False, methods=['get'], url_path='pending-invites')
    def pending_invites(self, request):
        """Return non-expired pending organization invitations for the current user."""
        now = timezone.now()
        invites = (
            OrganizationMembership.objects.filter(
                user=request.user,
                is_active=False,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .select_related('organization')
            .order_by('-invited_at')
        )
        return Response([
            {
                'id': str(inv.id),
                'organization': str(inv.organization_id),
                'organization_id': str(inv.organization_id),
                'organization_name': inv.organization.name,
                'organization_slug': inv.organization.slug,
                'role': inv.role,
                'invited_at': inv.invited_at,
                'expires_at': inv.expires_at,
            }
            for inv in invites
        ])

    @action(detail=True, methods=['post'], url_path='accept-invite')
    def accept_invite(self, request, pk=None):
        """Accept a pending organization invitation for the authenticated user."""
        org = Organization.objects.filter(pk=pk).first()
        if not org:
            return Response({'error': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            membership = (
                OrganizationMembership.objects.select_for_update()
                .filter(organization=org, user=request.user)
                .first()
            )
            if not membership:
                return Response({'error': 'Invitation not found.'}, status=status.HTTP_404_NOT_FOUND)
            if membership.expires_at and membership.expires_at <= timezone.now():
                return Response({'error': 'Invitation has expired.'}, status=status.HTTP_400_BAD_REQUEST)
            membership.is_active = True
            membership.accepted_at = timezone.now()
            membership.save(update_fields=['is_active', 'accepted_at'])

        return Response(OrganizationMembershipSerializer(membership).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        org = self.get_object()
        assert_admin(request.user, org)

        user_id = request.data.get('user_id')
        if not user_id:
            return Response({'error': 'user_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction
        now = timezone.now()
        with transaction.atomic():
            membership = (
                OrganizationMembership.objects.select_for_update()
                .filter(organization=org, user_id=user_id)
                .first()
            )
            if not membership:
                return Response({'error': 'User is not a member'}, status=status.HTTP_404_NOT_FOUND)

            if membership.role == OrganizationMembership.Role.OWNER:
                return Response({'error': 'Cannot remove the organization owner.'},
                                status=status.HTTP_400_BAD_REQUEST)

            # Don't remove the last active unexpired admin
            if membership.role == OrganizationMembership.Role.ADMIN and membership.is_active:
                admin_count = (
                    OrganizationMembership.objects.select_for_update()
                    .filter(
                        organization=org,
                        role=OrganizationMembership.Role.ADMIN,
                        is_active=True,
                    )
                    .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                    .count()
                )
                if admin_count <= 1:
                    return Response({'error': 'Cannot remove the last admin.'},
                                    status=status.HTTP_400_BAD_REQUEST)

            membership.delete()
        return Response({'message': 'Member removed.'})

    @action(detail=True, methods=['post'], url_path='change-role')
    def change_role(self, request, pk=None):
        org = self.get_object()
        assert_admin(request.user, org)

        user_id = request.data.get('user_id')
        new_role = request.data.get('role')
        if not user_id or not new_role:
            return Response({'error': 'user_id and role are required'}, status=status.HTTP_400_BAD_REQUEST)

        membership = OrganizationMembership.objects.filter(
            organization=org, user_id=user_id,
        ).first()
        if not membership:
            return Response({'error': 'User is not a member'}, status=status.HTTP_404_NOT_FOUND)

        if membership.role == OrganizationMembership.Role.OWNER:
            return Response({'error': 'Cannot change the owner\'s role.'},
                            status=status.HTTP_400_BAD_REQUEST)

        allowed_roles = {
            OrganizationMembership.Role.ADMIN,
            OrganizationMembership.Role.MEMBER,
        }
        if new_role not in allowed_roles:
            return Response(
                {'error': "role must be 'ADMIN' or 'MEMBER'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership.role = new_role
        membership.save(update_fields=['role'])
        return Response(OrganizationMembershipSerializer(membership).data)


    @action(detail=True, methods=['get', 'post'], url_path='registry')
    def registry(self, request, pk=None):
        """
        GET  /api/v1/organizations/{id}/registry/  — get org's scoped registry config
        POST /api/v1/organizations/{id}/registry/ — set org's scoped registry config

        POST body::
            {
                "registry_url": "my-registry.internal:5000",
                "username": "admin",
                "password": "...",
                "allowed_registry_hosts": ["my-registry.internal:5000"]
            }
        """
        from django.contrib.contenttypes.models import ContentType

        from apps.deployments.models.registry_scope import ScopedRegistry
        from apps.deployments.serializers.registry_scope import (
            ScopedRegistryReadSerializer,
            ScopedRegistrySerializer,
        )

        org = self.get_object()

        if request.method == 'POST':
            assert_admin(request.user, org)
            data = {**request.data, 'scope_type': 'organization', 'scope_id': str(org.id)}
            serializer = ScopedRegistrySerializer(data=data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            ct = ContentType.objects.get_for_model(org)
            existing = ScopedRegistry.objects.filter(
                content_type=ct, object_id=org.id
            ).first()

            if existing:
                for field, value in serializer.validated_data.items():
                    if field not in ('scope_type', 'scope_id', 'content_type', 'object_id'):
                        setattr(existing, field, value)
                existing.save()
                logger.info("Updated scoped registry for organization %s", org.id)
                return Response({'status': 'updated', 'id': str(existing.id)})
            else:
                instance = serializer.save()
                logger.info("Created scoped registry for organization %s", org.id)
                return Response({'status': 'created', 'id': str(instance.id)},
                                status=status.HTTP_201_CREATED)

        # GET: return effective registry config
        creds = ScopedRegistry.resolve_registry_credentials(org)
        scoped = ScopedRegistry.get_for_object(org)
        read_ser = ScopedRegistryReadSerializer(scoped) if scoped else None

        return Response({
            'effective_url': creds.get('url', ''),
            'has_username': bool(creds.get('username')),
            'has_password': bool(creds.get('password')),
            'is_scoped': scoped is not None,
            'scoped_config': read_ser.data if read_ser else None,
            'hierarchy': ['organization', 'platform'],
        })


class OrganizationSSOViewSet(viewsets.ModelViewSet):
    queryset = OrganizationSSO.objects.all()
    serializer_class = OrganizationSSOSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        now = timezone.now()
        return self.queryset.filter(
            Q(organization__owner=self.request.user)
            | (
                Q(
                    organization__memberships__user=self.request.user,
                    organization__memberships__is_active=True,
                )
                & (
                    Q(organization__memberships__expires_at__isnull=True)
                    | Q(organization__memberships__expires_at__gt=now)
                )
            )
        ).distinct()

    def perform_create(self, serializer):
        org = serializer.validated_data.get('organization')
        if org is None:
            raise ValidationError({'organization': 'This field is required.'})
        assert_admin(self.request.user, org)
        if str(serializer.validated_data.get('default_role') or '').upper() == 'OWNER':
            raise ValidationError({'default_role': 'default_role cannot be OWNER.'})
        serializer.save()

    def perform_update(self, serializer):
        assert_admin(self.request.user, serializer.instance.organization)
        if str(serializer.validated_data.get('default_role') or '').upper() == 'OWNER':
            raise ValidationError({'default_role': 'default_role cannot be OWNER.'})
        serializer.save()

    def perform_destroy(self, instance):
        assert_admin(self.request.user, instance.organization)
        instance.delete()
