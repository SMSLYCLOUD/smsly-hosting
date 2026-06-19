"""Organization API views."""
import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Organization, OrganizationMembership, OrganizationSSO
from .permissions import get_org_q_filter, assert_admin, assert_owner
from .serializers import (
    OrganizationSerializer, OrganizationMembershipSerializer,
    InviteMemberSerializer, OrganizationSSOSerializer,
)

logger = logging.getLogger(__name__)


class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(get_org_q_filter(self.request.user)).prefetch_related('memberships')

    def perform_destroy(self, instance):
        assert_owner(self.request.user, instance)
        instance.delete()

    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        org = self.get_object()
        members = OrganizationMembership.objects.filter(organization=org).select_related('user')
        return Response(OrganizationMembershipSerializer(members, many=True).data)

    @action(detail=True, methods=['post'])
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
                {'error': 'Invitation could not be sent. Ensure the email is registered.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership, created = OrganizationMembership.objects.get_or_create(
            organization=org, user=user,
            defaults={'role': role},
        )
        if not created:
            return Response(
                {'error': f'{email} is already a member of this organization.'},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(OrganizationMembershipSerializer(membership).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        org = self.get_object()
        assert_admin(request.user, org)

        user_id = request.data.get('user_id')
        if not user_id:
            return Response({'error': 'user_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        membership = OrganizationMembership.objects.filter(
            organization=org, user_id=user_id,
        ).first()
        if not membership:
            return Response({'error': 'User is not a member'}, status=status.HTTP_404_NOT_FOUND)

        if membership.role == OrganizationMembership.Role.OWNER:
            return Response({'error': 'Cannot remove the organization owner.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Don't remove the last admin
        if membership.role == OrganizationMembership.Role.ADMIN:
            admin_count = OrganizationMembership.objects.filter(
                organization=org, role=OrganizationMembership.Role.ADMIN,
            ).count()
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

        membership.role = new_role
        membership.save(update_fields=['role'])
        return Response(OrganizationMembershipSerializer(membership).data)


class OrganizationSSOViewSet(viewsets.ModelViewSet):
    queryset = OrganizationSSO.objects.all()
    serializer_class = OrganizationSSOSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(organization__memberships__user=self.request.user)
