"""Generic /api/v1/domains/ ViewSet.

Operates on the ``apps.domains.models.Domain`` model so the frontend's
``/domains`` page can list, create, update, and delete custom domains
across all services owned by the requesting user.
"""
import logging

from django.db import IntegrityError
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from ..models import Domain, DomainStatus

logger = logging.getLogger(__name__)


class GlobalDomainSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='domain_name', read_only=True)
    service = serializers.SerializerMethodField()
    service_name = serializers.SerializerMethodField()
    dns_managed = serializers.SerializerMethodField()
    ssl_enabled = serializers.SerializerMethodField()
    record_type = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()

    class Meta:
        model = Domain
        fields = [
            'id',
            'name',
            'service',
            'service_name',
            'status',
            'ssl_enabled',
            'dns_managed',
            'record_type',
            'target',
            'verified',
            'created_at',
        ]

    def get_service(self, obj):
        return str(obj.service_id) if obj.service_id else None

    def get_service_name(self, obj):
        return obj.service.name if obj.service_id else None

    def get_dns_managed(self, obj):
        return False

    def get_ssl_enabled(self, obj):
        return bool(getattr(obj, 'ssl_active', False))

    def get_record_type(self, obj):
        return 'CNAME'

    def get_target(self, obj):
        return obj.dns_expected or ''


class GlobalDomainViewSet(viewsets.ModelViewSet):
    """Generic /api/v1/domains/ endpoint.

    Supports list/create/retrieve/update/destroy on the global Domain
    model, scoped to the requesting user. Creates require a service_id
    that the user owns.
    """
    serializer_class = GlobalDomainSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        user = self.request.user
        if user.is_superuser:
            return Domain.objects.all().select_related('service').order_by('-created_at')
        return Domain.objects.filter(
            Q(service__owner=user) | Q(service__project__team__members__user=user)
        ).select_related('service').distinct().order_by('-created_at')

    def create(self, request, *args, **kwargs):
        from apps.deployments.models.core import Service
        domain_name = (request.data.get('name') or request.data.get('domain_name') or '').strip().lower()
        service_id = request.data.get('service') or request.data.get('service_id')
        if not domain_name:
            return Response(
                {'error': 'Domain name is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not service_id:
            return Response(
                {'error': 'A target service is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        service = Service.objects.filter(id=service_id).first()
        if not service:
            return Response(
                {'error': 'Service not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not (request.user.is_superuser
                or service.owner_id == request.user.id
                or (service.project_id and service.project.team_id
                    and service.project.team.members.filter(user=request.user).exists())):
            raise PermissionDenied("You do not have access to this service.")

        try:
            domain, created = Domain.objects.get_or_create(
                domain_name=domain_name,
                defaults={'service': service, 'status': DomainStatus.PENDING},
            )
        except IntegrityError:
            Domain.objects.filter(domain_name=domain_name).first()
            return Response(
                {'error': f'Domain {domain_name} is already registered.'},
                status=status.HTTP_409_CONFLICT,
            )

        if not created and domain.service_id != service.id:
            return Response(
                {'error': f'Domain {domain_name} is already registered to another service.'},
                status=status.HTTP_409_CONFLICT,
            )

        if created:
            try:
                from ..tasks import verify_dns_and_provision_ssl_task
                verify_dns_and_provision_ssl_task.delay(domain.id)
            except Exception as exc:
                logger.warning("Failed to dispatch domain verification for %s: %s", domain_name, exc)

        return Response(self.get_serializer(domain).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        from apps.deployments.models.core import Service
        from django.db.models import Q
        if not (request.user.is_superuser
                or (instance.service_id
                    and (Service.objects.filter(
                        Q(id=instance.service_id),
                        Q(owner=request.user)
                        | Q(project__team__members__user=request.user),
                    ).exists()))):
            raise PermissionDenied("You do not have access to this domain.")
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
