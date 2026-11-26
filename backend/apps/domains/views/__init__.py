"""Generic /api/v1/domains/ ViewSet.

Operates on the ``apps.domains.models.Domain`` model so the frontend's
``/domains`` page can list, create, retrieve, and delete custom domains
across all services owned by the requesting user.
"""
import logging

from django.db import IntegrityError
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from ..models import Domain, DomainStatus

logger = logging.getLogger(__name__)


def _find_domain_conflict(domain_name: str, exclude_service_id=None):
    """Return a conflicting Service if *domain_name* is already assigned elsewhere."""
    from apps.deployments.models.core import Service

    qs = Service.objects.all()
    if exclude_service_id is not None:
        qs = qs.exclude(id=exclude_service_id)

    public_conflict = qs.filter(public_domain=domain_name).only("id", "name").first()
    if public_conflict:
        return public_conflict

    dom_qs = Domain.objects.filter(domain_name=domain_name)
    if exclude_service_id is not None:
        dom_qs = dom_qs.exclude(service_id=exclude_service_id)
    existing_dom = dom_qs.select_related("service").first()
    if existing_dom and existing_dom.service_id:
        return existing_dom.service

    normalized = str(domain_name or "").strip().lower()
    if normalized:
        for other in qs.only("id", "name", "custom_domains", "host_aliases").iterator():
            for item in (other.custom_domains or []):
                if isinstance(item, str) and item.strip().lower() == normalized:
                    return other
            for entry in (other.host_aliases or []):
                if isinstance(entry, dict) and str(entry.get("host") or "").strip().lower() == normalized:
                    return other
    return None


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
        read_only_fields = ['id', 'status', 'verified', 'created_at']

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


class GlobalDomainViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Generic /api/v1/domains/ endpoint.

    Supports list/create/retrieve/destroy on the global Domain model,
    scoped to the requesting user. Creates require a service_id that the
    user has write access to. PUT/PATCH are disabled to prevent status
    or verification tampering.
    """
    serializer_class = GlobalDomainSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        from apps.teams.permissions import get_team_q_filter

        user = self.request.user
        if user.is_superuser:
            return Domain.objects.all().select_related('service').order_by('-created_at')
        return Domain.objects.filter(
            get_team_q_filter(user, prefix="service__")
        ).select_related('service').distinct().order_by('-created_at')

    def create(self, request, *args, **kwargs):
        from ..utils import normalize_domain
        from apps.deployments.models.core import Service
        from apps.deployments.views._helpers import _is_platform_owned_domain
        from apps.teams.permissions import assert_can_write

        try:
            domain_name = normalize_domain(
                request.data.get('name') or request.data.get('domain_name') or ''
            )
        except ValueError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        service_id = request.data.get('service') or request.data.get('service_id')
        if not service_id:
            return Response(
                {'error': 'A target service is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            service = Service.objects.filter(id=service_id).first()
        except (DjangoValidationError, ValueError):
            return Response(
                {'error': 'Invalid service ID.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not service:
            return Response(
                {'error': 'Service not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        assert_can_write(request.user, service)

        if _is_platform_owned_domain(domain_name):
            return Response(
                {'error': 'This domain belongs to the platform and cannot be claimed as a custom domain.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        conflict = _find_domain_conflict(domain_name, exclude_service_id=service.id)
        if conflict:
            return Response(
                {'error': f'Domain {domain_name} is already assigned to service "{conflict.name}".'},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            domain, created = Domain.objects.get_or_create(
                domain_name=domain_name,
                defaults={'service': service, 'status': DomainStatus.PENDING},
            )
        except IntegrityError:
            return Response(
                {'error': f'Domain {domain_name} is already registered.'},
                status=status.HTTP_409_CONFLICT,
            )

        if not created and domain.service_id != service.id:
            return Response(
                {'error': f'Domain {domain_name} is already registered to another service.'},
                status=status.HTTP_409_CONFLICT,
            )

        custom_domains = [
            d for d in (service.custom_domains or [])
            if isinstance(d, str) and d.strip()
        ]
        if domain_name not in custom_domains:
            custom_domains.append(domain_name)
            service.custom_domains = custom_domains
            service.save(update_fields=['custom_domains'])

        if created:
            try:
                from ..tasks import verify_dns_and_provision_ssl_task
                verify_dns_and_provision_ssl_task.delay(domain.id)
            except Exception as exc:
                logger.warning("Failed to dispatch domain verification for %s: %s", domain_name, exc)

        return Response(self.get_serializer(domain).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.service_id:
            from apps.teams.permissions import assert_can_delete
            assert_can_delete(request.user, instance.service)
        elif not request.user.is_superuser:
            raise PermissionDenied("You do not have access to this domain.")
        domain_name = instance.domain_name
        service = instance.service
        instance.delete()
        if service and service.custom_domains:
            domains = [
                d for d in service.custom_domains
                if isinstance(d, str) and d.strip() and d != domain_name
            ]
            if len(domains) != len(service.custom_domains):
                service.custom_domains = domains
                service.save(update_fields=['custom_domains'])
        try:
            from ..tasks import _trigger_caddy_reload
            _trigger_caddy_reload()
        except Exception as exc:
            logger.warning("Failed to reload Caddy after domain removal: %s", exc)
        return Response(status=status.HTTP_204_NO_CONTENT)
