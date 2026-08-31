"""
Views for Project CRUD and nested service management.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from rest_framework import permissions, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.permissions.codes import MEMBER_INVITE, MEMBER_REMOVE, MEMBER_ROLE_CHANGE
from apps.permissions.utils import has_permission

from apps.deployments.models import Service  # type: ignore[attr-defined]
from ..models.project import Project, ProjectMember  # type: ignore[attr-defined]
from apps.deployments.serializers import ServiceSerializer

logger = logging.getLogger(__name__)


class RemoveServiceSerializer(drf_serializers.Serializer):
    service_id = drf_serializers.UUIDField()
    replacement_project_id = drf_serializers.UUIDField()


class ProjectViewSet(viewsets.ModelViewSet):
    """
    CRUD for Projects.  Owner-scoped — users can only see/manage their own.

    Endpoints:
        GET    /api/v1/projects/                    — list
        POST   /api/v1/projects/                    — create
        GET    /api/v1/projects/{id}/                — detail
        PATCH  /api/v1/projects/{id}/                — update
        DELETE /api/v1/projects/{id}/                — delete
        GET    /api/v1/projects/{id}/services/       — services in project
        POST   /api/v1/projects/{id}/move-service/   — move service into project
        POST   /api/v1/projects/{id}/remove-service/ — remove service from project
    """
    permission_classes = [permissions.IsAuthenticated]

    # Serializer defined inline to keep it co-located with the viewset

    class ProjectSerializer(drf_serializers.ModelSerializer):
        services_count = drf_serializers.SerializerMethodField()
        latest_deploy_status = drf_serializers.SerializerMethodField()
        latest_deploy_at = drf_serializers.SerializerMethodField()

        class Meta:
            model = Project
            fields = [
                'id', 'name', 'slug', 'description',
                'icon_emoji', 'color', 'is_default', 'is_ephemeral',
                'services_count', 'latest_deploy_status', 'latest_deploy_at',
                'created_at', 'updated_at',
                # Internal network (project-scoped Docker bridge).
                # Operators can override the platform default by setting a
                # specific CIDR (e.g. 10.99.0.0/24). Empty string falls
                # back to PlatformConfig.default_internal_subnet.
                'internal_subnet',
            ]
            read_only_fields = ['id', 'slug', 'created_at', 'updated_at']
            # Disable DRF's auto UniqueTogetherValidator for (owner, slug)
            # because slug is auto-generated + deduplicated in Project.save()
            validators: list = []

        def get_services_count(self, obj):
            counts = getattr(self, '_service_counts', None)
            if counts is not None:
                return counts.get(obj.id, 0)
            return obj.services.count()

        def get_latest_deploy_status(self, obj):
            statuses = getattr(self, '_latest_statuses', None)
            if statuses is not None:
                return statuses.get(obj.id)
            from apps.deployments.models import Deployment
            dep = (
                Deployment.objects
                .filter(service__project=obj)
                .order_by('-created_at')
                .values('status')
                .first()
            )
            return dep['status'] if dep else None

        def get_latest_deploy_at(self, obj):
            times = getattr(self, '_latest_times', None)
            if times is not None:
                return times.get(obj.id)
            from apps.deployments.models import Deployment
            dep = (
                Deployment.objects
                .filter(service__project=obj)
                .order_by('-created_at')
                .values('created_at')
                .first()
            )
            return dep['created_at'].isoformat() if dep else None

        @classmethod
        def prefetch_for_list(cls, queryset):
            """Attach bulk-fetched data to avoid N+1 in list views."""
            from django.db.models import Count, Max
            from apps.deployments.models import Deployment
            from apps.deployments.models import Service

            project_ids = list(queryset.values_list('id', flat=True))

            service_counts = dict(
                Service.objects.filter(project_id__in=project_ids)
                .values('project_id')
                .annotate(cnt=Count('id'))
                .values_list('project_id', 'cnt')
            )

            latest_deps_qs = list(
                Deployment.objects.filter(service__project_id__in=project_ids)
                .values('service__project_id')
                .annotate(latest_status=Max('status'), latest_at=Max('created_at'))
                .values_list('service__project_id', 'latest_status', 'latest_at')
            )

            instance = cls()
            instance._service_counts = service_counts
            instance._latest_statuses = {pid: status for pid, status, _ in latest_deps_qs}
            instance._latest_times = {
                pid: at.isoformat() if at else None
                for pid, _, at in latest_deps_qs
            }
            return instance

    serializer_class = ProjectSerializer

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            page_ids = [obj.id for obj in page]
            filtered_qs = queryset.model.objects.filter(id__in=page_ids)
            serializer = self.ProjectSerializer.prefetch_for_list(filtered_qs)
            data = [serializer.to_representation(item) for item in page]
            return self.get_paginated_response(data)
        serializer = self.ProjectSerializer.prefetch_for_list(queryset)
        data = [serializer.to_representation(item) for item in queryset]
        return Response(data)

    def get_queryset(self):
        """Owner and Team scoped: users see their own projects and team projects.

        All projects (including ephemeral) are shown by default.
        Pass ``?include_ephemeral=false`` to hide ephemeral projects.
        """
        qs = Project.objects.all().order_by('id')
        if self.request.user.is_superuser:
            qs = qs
        else:
            qs = qs.filter(
                Q(owner=self.request.user) |
                Q(team__members__user=self.request.user)
            ).distinct().order_by('id')

        # Include all projects by default; optionally exclude ephemeral
        include_ephemeral = self.request.query_params.get('include_ephemeral', '').lower() not in ('false', '0', 'no')
        if not include_ephemeral:
            qs = qs.filter(is_ephemeral=False)

        return qs

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    # ── Nested: services in project ──────────────────────────────

    @action(detail=True, methods=['get'], url_path='services')
    def project_services(self, request, pk=None):
        """GET /api/v1/projects/{id}/services/ — list services in this project."""
        project = self.get_object()
        services = Service.objects.filter(
            project=project,
        ).order_by('-updated_at')
        serializer = ServiceSerializer(services, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='move-service')
    def move_service(self, request, pk=None):
        """
        POST /api/v1/projects/{id}/move-service/
        Body: { "service_id": "uuid" }
        """
        from django.db.models import Q
        project = self.get_object()
        service_id = request.data.get('service_id')
        if not service_id:
            return Response(
                {'error': 'service_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            service = Service.objects.get(
                Q(owner=request.user) | Q(project__team__members__user=request.user),
                id=service_id
            )
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found or access denied'},
                status=status.HTTP_404_NOT_FOUND,
            )

        service.project = project
        service.save(update_fields=['project', 'updated_at'])

        logger.info(
            'Moved service %s (%s) into project %s (%s)',
            service.name, service.id, project.name, project.id,
        )
        return Response({
            'status': 'ok',
            'service_id': str(service.id),
            'project_id': str(project.id),
        })

    @action(detail=True, methods=['post'], url_path='remove-service')
    def remove_service(self, request, pk=None):
        """
        POST /api/v1/projects/{id}/remove-service/
        Body: { "service_id": "uuid", "replacement_project_id": "uuid" }

        SECURITY (Issue 50): unlinking a service from its project would
        orphan it — license tier checks and billing assume
        ``service.project`` is non-null. The endpoint therefore requires
        a ``replacement_project_id`` and re-attaches the service to that
        project in the same DB transaction. The original caller's
        project is verified as the service's *current* project, the
        replacement project is verified as accessible to the caller,
        and the service is moved atomically.
        """
        serializer = RemoveServiceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': 'service_id and replacement_project_id are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service_id = serializer.validated_data['service_id']
        replacement_project_id = serializer.validated_data['replacement_project_id']

        project = self.get_object()

        try:
            with transaction.atomic():
                service = Service.objects.select_for_update().get(
                    Q(owner=request.user)
                    | Q(project__team__members__user=request.user),
                    id=service_id,
                )
                if service.project_id != project.id:
                    return Response(
                        {'error': 'Service is not in this project'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                replacement_qs = Project.objects.filter(id=replacement_project_id)
                if not request.user.is_superuser:
                    replacement_qs = replacement_qs.filter(
                        Q(owner=request.user)
                        | Q(team__members__user=request.user)
                    )
                if not replacement_qs.exists():
                    return Response(
                        {'error': 'Replacement project not found or access denied'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                service.project_id = replacement_project_id
                service.save(update_fields=['project', 'updated_at'])
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found or access denied'},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            'Moved service %s (%s) from project %s (%s) to replacement %s',
            service.name, service.id, project.name, project.id, replacement_project_id,
        )
        return Response({
            'status': 'ok',
            'service_id': str(service.id),
            'project_id': str(project.id),
            'replacement_project_id': str(replacement_project_id),
        })

    @action(detail=True, methods=['get', 'post'], url_path='registry')
    def registry(self, request, pk=None):
        """
        GET  /api/v1/projects/{id}/registry/  — get project's scoped registry
        POST /api/v1/projects/{id}/registry/ — set project's scoped registry

        POST body::
            {
                "registry_url": "my-registry.internal:5000",
                "username": "admin",
                "password": "...",
                "allowed_registry_hosts": ["my-registry.internal:5000"]
            }

        Returns the effective registry config (walks hierarchy if none set).
        """
        from django.contrib.contenttypes.models import ContentType

        from apps.deployments.models.registry_scope import ScopedRegistry
        from ..serializers_registry_scope import (
            ScopedRegistryReadSerializer,
            ScopedRegistrySerializer,
        )

        project = self.get_object()

        if request.method == 'POST':
            data = {**request.data, 'scope_type': 'project', 'scope_id': str(project.id)}
            serializer = ScopedRegistrySerializer(data=data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            ct = ContentType.objects.get_for_model(project)

            # Update or create
            existing = ScopedRegistry.objects.filter(
                content_type=ct, object_id=project.id
            ).first()

            if existing:
                for field, value in serializer.validated_data.items():
                    if field not in ('scope_type', 'scope_id', 'content_type', 'object_id'):
                        # Guard: skip empty password to avoid overwriting stored credentials.
                        # The frontend sends password=undefined when the user leaves the
                        # field blank, but the API should also protect against explicit
                        # empty-string payloads.
                        if field == 'password' and not value:
                            continue
                        setattr(existing, field, value)
                existing.save()
                logger.info("Updated scoped registry for project %s", project.id)
                return Response({'status': 'updated', 'id': str(existing.id)})
            else:
                instance = serializer.save()
                logger.info("Created scoped registry for project %s", project.id)
                return Response({'status': 'created', 'id': str(instance.id)},
                                status=status.HTTP_201_CREATED)

        # GET: return effective registry config (walks hierarchy)
        creds = ScopedRegistry.resolve_registry_credentials(project)
        scoped = ScopedRegistry.get_for_object(project)
        read_ser = ScopedRegistryReadSerializer(scoped) if scoped else None

        return Response({
            'effective_url': creds.get('url', ''),
            'has_username': bool(creds.get('username')),
            'has_password': bool(creds.get('password')),
            'is_scoped': scoped is not None,
            'scoped_config': read_ser.data if read_ser else None,
            'hierarchy': ['project', 'team', 'organization', 'platform'],
        })

    @action(detail=True, methods=['get', 'post'], url_path='internal-network')
    def internal_network(self, request, pk=None):
        """
        GET  /api/v1/projects/{id}/internal-network/ — current network state
        POST /api/v1/projects/{id}/internal-network/ — provision now

        GET returns whether the project has a scoped Docker bridge, its
        name/subnet, and how many services are attached.

        POST (idempotent) creates the ScopedNetwork DB record + the actual
        Docker bridge if missing, then attaches every RUNNING service
        container in the project to it. Use this when Project.internal_subnet
        is set (or the platform default applies) but no bridge exists yet —
        e.g. a manual project whose services were deployed before the
        internal-network feature. No re-deploy needed: live containers get
        dual-homed in place via docker network connect.
        """
        from django.contrib.contenttypes.models import ContentType

        project = self.get_object()
        ct = ContentType.objects.get_for_model(project)

        from apps.deployments.models.network_scope import ScopedNetwork
        scoped = ScopedNetwork.objects.filter(
            content_type=ct, object_id=project.id, is_active=True
        ).first()

        def _state_response(status_label: str, http_status=200):
            running = 0
            attached = 0
            try:
                import docker as docker_lib
                client = docker_lib.from_env()
                for svc in project.services.all():
                    try:
                        container = client.containers.get(svc.name)
                        container.reload()
                        if (container.attrs.get('State') or {}).get('Status') == 'running':
                            running += 1
                            nets = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
                            if scoped and scoped.network_name in nets:
                                attached += 1
                    except Exception:
                        continue
            except Exception:
                pass
            return Response({
                'status': status_label,
                'exists': scoped is not None,
                'network_name': scoped.network_name if scoped else '',
                'subnet': scoped.subnet if scoped else '',
                'isolated': bool(scoped.isolated) if scoped else False,
                'services_running': running,
                'services_attached': attached,
            }, status=http_status)

        def _attach_running_containers(client, net, network_name):
            """Attach the project's running service containers AND its ACTIVE
            addon containers to *net*.

            Services need their DB/redis reachable on the project bridge or
            the first request after attaching fails with a DNS error; addons
            connect under their alias so existing connection URLs keep
            resolving. The addon lookup uses the service-scoped rule (own +
            '-shared' only) so we never steal another project's personal
            addons.
            """
            attached_services = 0
            attached_addons = 0
            for svc in project.services.all():
                try:
                    container = client.containers.get(svc.name)
                    container.reload()
                    nets = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
                    if (container.attrs.get('State') or {}).get('Status') == 'running' \
                            and network_name not in nets:
                        net.connect(container, aliases=[svc.name, f"{svc.name}.default.internal"])
                        attached_services += 1
                except Exception:
                    continue
            try:
                from django.db.models import Q
                from apps.deployments.models.addons import Addon
                addon_qs = Addon.objects.filter(status=Addon.Status.ACTIVE).filter(
                    Q(service__project=project) | Q(service__project=project, name__endswith='-shared')
                )
                for addon in addon_qs:
                    try:
                        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
                        container = client.containers.get(container_name)
                        container.reload()
                        nets = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
                        if (container.attrs.get('State') or {}).get('Status') == 'running' \
                                and network_name not in nets:
                            alias = addon.name or f"{addon.addon_type.lower()}-shared"
                            net.connect(container, aliases=[alias])
                            attached_addons += 1
                    except Exception:
                        continue
            except Exception as exc:
                logger.debug("Addon attach during network provisioning skipped: %s", exc)
            return attached_services, attached_addons

        if request.method == 'GET':
            return _state_response('ok')

        # POST: provision
        if scoped:
            # Already exists — attach any running containers that aren't on
            # it yet (idempotent backfill).
            attached_services, attached_addons = 0, 0
            platform_services, platform_addons = 0, 0
            try:
                import docker as docker_lib
                client = docker_lib.from_env()
                try:
                    client.networks.get(scoped.network_name)
                except docker_lib.errors.NotFound:
                    # DB record exists but the bridge was lost (host reboot,
                    # docker prune) — recreate it.
                    client.networks.create(
                        scoped.network_name,
                        driver=scoped.driver or 'bridge',
                        internal=bool(scoped.internal),
                        enable_ipv6=bool(scoped.enable_ipv6),
                        **({'subnet': scoped.subnet} if scoped.subnet else {}),
                    )
                    logger.info("Recreated missing Docker bridge %s for project %s",
                                scoped.network_name, project.id)
                net = client.networks.get(scoped.network_name)
                attached_services, attached_addons = _attach_running_containers(
                    client, net, scoped.network_name
                )

                # Dual-network backfill: same option as the create path —
                # bring services + addons onto the platform bridge too.
                dual_platform = True
                if isinstance(request.data, dict):
                    raw_dual = request.data.get('dual_platform')
                    if raw_dual is not None:
                        dual_platform = str(raw_dual).strip().lower() not in ('false', '0', 'no', 'off')
                if dual_platform:
                    try:
                        from apps.deployments.services.network_scope import (
                            attach_container_to_platform_bridge,
                            ensure_platform_bridge,
                        )
                        platform_bridge_name = ensure_platform_bridge()
                        platform_net = client.networks.get(platform_bridge_name)
                        for svc in project.services.all():
                            try:
                                container = client.containers.get(svc.name)
                                container.reload()
                                if (container.attrs.get('State') or {}).get('Status') == 'running' \
                                        and attach_container_to_platform_bridge(container.id, svc.name):
                                    platform_services += 1
                            except Exception:
                                continue
                        try:
                            from django.db.models import Q as _Q
                            from apps.deployments.models.addons import Addon as _Addon
                            addon_qs = _Addon.objects.filter(status=_Addon.Status.ACTIVE).filter(
                                _Q(service__project=project)
                                | _Q(service__project=project, name__endswith='-shared')
                            )
                            for addon in addon_qs:
                                try:
                                    container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
                                    container = client.containers.get(container_name)
                                    container.reload()
                                    nets = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
                                    if (container.attrs.get('State') or {}).get('Status') == 'running' \
                                            and platform_bridge_name not in nets:
                                        alias = addon.name or f"{addon.addon_type.lower()}-shared"
                                        platform_net.connect(container, aliases=[alias])
                                        platform_addons += 1
                                except Exception:
                                    continue
                        except Exception as exc:
                            logger.debug("Addon platform-bridge backfill skipped: %s", exc)
                    except Exception as exc:
                        logger.debug("Platform-bridge backfill skipped: %s", exc)
            except Exception as exc:
                return Response(
                    {'error': f'Failed to backfill bridge attachment: {exc}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            logger.info(
                "Backfilled project bridge %s for project %s "
                "(%d services + %d addons; %d + %d dual-homed to platform bridge)",
                scoped.network_name, project.id,
                attached_services, attached_addons, platform_services, platform_addons,
            )
            return _state_response(
                'existing'
                if attached_services == 0 and attached_addons == 0
                and platform_services == 0 and platform_addons == 0
                else 'backfilled'
            )

        # No ScopedNetwork — create record + Docker bridge + attach services
        try:
            import secrets as _secrets
            scope_id = str(project.id).replace('-', '')[:8]
            network_name = f"smsly-net-{scope_id}"

            # Resolve subnet: project override → platform default → hardcoded
            subnet = (getattr(project, 'internal_subnet', '') or '').strip()
            if not subnet:
                from apps.deployments.models.core import PlatformConfig
                subnet = (PlatformConfig.load().default_internal_subnet or '').strip() \
                    or '172.30.224.0/24'

            import docker as docker_lib
            client = docker_lib.from_env()
            # Create the bridge (tolerate a race)
            try:
                net = client.networks.create(
                    network_name,
                    driver='bridge',
                    subnet=subnet,
                    labels={
                        'smsly.scope': 'project',
                        'smsly.project_id': str(project.id),
                    },
                )
                logger.info("Created Docker bridge %s (%s) for project %s",
                            network_name, subnet, project.id)
            except docker_lib.errors.APIError as exc:
                if 'already exists' not in str(exc).lower():
                    raise
                net = client.networks.get(network_name)

            # Create the DB record
            scoped = ScopedNetwork.objects.create(
                content_type=ct,
                object_id=project.id,
                network_name=network_name,
                driver='bridge',
                isolated=True,
                internal=False,
                enable_ipv6=False,
                subnet=subnet,
                allow_public_traefik=True,
                is_active=True,
            )

            # Attach every running service container + the project's ACTIVE
            # addon containers to the new project bridge (shared helper
            # defined alongside _state_response above).
            attached_services, attached_addons = _attach_running_containers(
                client, net, network_name
            )

            # Dual-network option: attach services AND their addons to the
            # platform-wide bridge (smsly-platform-net) so cross-project
            # traffic stays host-internal too. Addons dual-home with the
            # same alias so a connection URL resolved from either bridge
            # works. Enabled by default (the whole point of the internal
            # network is to keep traffic off public DNS); disable with
            # {"dual_platform": false} in the POST body.
            dual_platform = True
            if isinstance(request.data, dict):
                raw_dual = request.data.get('dual_platform')
                if raw_dual is not None:
                    dual_platform = str(raw_dual).strip().lower() not in ('false', '0', 'no', 'off')

            platform_services = 0
            platform_addons = 0
            if dual_platform:
                try:
                    from apps.deployments.services.network_scope import (
                        attach_container_to_platform_bridge,
                        ensure_platform_bridge,
                    )
                    platform_bridge_name = ensure_platform_bridge()
                    platform_net = client.networks.get(platform_bridge_name)
                    for svc in project.services.all():
                        try:
                            container = client.containers.get(svc.name)
                            container.reload()
                            if (container.attrs.get('State') or {}).get('Status') == 'running' \
                                    and attach_container_to_platform_bridge(container.id, svc.name):
                                platform_services += 1
                        except Exception:
                            continue
                    # Addons dual-home too — a cross-project service
                    # reaching for this project's shared postgres should
                    # resolve it on the platform bridge without any public
                    # hop. Uses the same service-scoped addon lookup as
                    # _attach_running_containers (own + '-shared').
                    try:
                        from django.db.models import Q as _Q
                        from apps.deployments.models.addons import Addon as _Addon
                        addon_qs = _Addon.objects.filter(status=_Addon.Status.ACTIVE).filter(
                            _Q(service__project=project)
                            | _Q(service__project=project, name__endswith='-shared')
                        )
                        for addon in addon_qs:
                            try:
                                container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
                                container = client.containers.get(container_name)
                                container.reload()
                                nets = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
                                if (container.attrs.get('State') or {}).get('Status') == 'running' \
                                        and platform_bridge_name not in nets:
                                    alias = addon.name or f"{addon.addon_type.lower()}-shared"
                                    platform_net.connect(container, aliases=[alias])
                                    platform_addons += 1
                            except Exception:
                                continue
                    except Exception as exc:
                        logger.debug("Addon platform-bridge attach skipped: %s", exc)
                except Exception as exc:
                    logger.debug("Platform-bridge attach during provision skipped: %s", exc)

            logger.info(
                "Provisioned internal network %s for project %s "
                "(%d services + %d addons on project bridge; "
                "%d services + %d addons dual-homed to platform bridge)",
                network_name, project.id,
                attached_services, attached_addons,
                platform_services, platform_addons,
            )
            return _state_response('created', status.HTTP_201_CREATED)

        except Exception as exc:
            logger.exception("Failed to provision internal network for project %s", project.id)
            return Response(
                {'error': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['post'], url_path='sync-envs')
    def sync_envs(self, request, pk=None):
        """
        POST /api/v1/projects/{id}/sync-envs/

        Hardens and synchronizes environment variables across all services in the ecosystem.
        Deterministic linking of Intelligence, Security, and Core services.
        """
        project = self.get_object()
        from apps.deployments.services.ecosystem import sync_ecosystem_envs

        try:
            logger.info("Triggering instant ecosystem sync for project %s (%s)", project.name, project.id)
            result = sync_ecosystem_envs(str(project.id))
            return Response(result)
        except Exception as e:
            logger.exception("Ecosystem sync failed for project %s", project.id)
            return Response(
                {"error": f"Sync failed: {e!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    # ── Project Members ───────────────────────────────────────────

    @action(detail=True, methods=['get'], url_path='members')
    def project_members(self, request, pk=None):
        """GET /api/v1/projects/{id}/members/ — list project members."""
        project = self.get_object()
        members = ProjectMember.objects.filter(project=project).select_related('user').order_by('role', 'user__username')

        class ProjectMemberSerializer(drf_serializers.ModelSerializer):
            username = drf_serializers.CharField(source='user.username', read_only=True)
            email = drf_serializers.EmailField(source='user.email', read_only=True)

            class Meta:
                model = ProjectMember
                fields = ('id', 'user', 'username', 'email', 'role', 'permissions', 'expires_at', 'joined_at')
                read_only_fields = ('user', 'joined_at')

        return Response(ProjectMemberSerializer(members, many=True).data)

    @action(detail=True, methods=['post'], url_path='members/invite')
    def invite_project_member(self, request, pk=None):
        """POST /api/v1/projects/{id}/members/invite/ — add a member to this project."""
        project = self.get_object()

        if not has_permission(request.user, project, MEMBER_INVITE):
            return Response({'error': 'You do not have permission to invite project members'}, status=status.HTTP_403_FORBIDDEN)

        email = request.data.get('email', '').strip()
        role = request.data.get('role', ProjectMember.Role.MEMBER)
        expires_at = request.data.get('expires_at')
        permissions_override = request.data.get('permissions')

        User = get_user_model()
        try:
            invitee = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        if ProjectMember.objects.filter(project=project, user=invitee).exists():
            return Response({'error': 'User already a project member'}, status=status.HTTP_400_BAD_REQUEST)

        member = ProjectMember.objects.create(
            project=project,
            user=invitee,
            role=role if role in dict(ProjectMember.Role.choices) else ProjectMember.Role.MEMBER,
            expires_at=expires_at if expires_at else None,
            permissions=permissions_override if isinstance(permissions_override, list) else [],
        )

        class ProjectMemberSerializer(drf_serializers.ModelSerializer):
            username = drf_serializers.CharField(source='user.username', read_only=True)
            email = drf_serializers.EmailField(source='user.email', read_only=True)

            class Meta:
                model = ProjectMember
                fields = ('id', 'user', 'username', 'email', 'role', 'permissions', 'expires_at', 'joined_at')
                read_only_fields = ('user', 'joined_at')

        return Response(ProjectMemberSerializer(member).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path=r'members/(?P<member_id>[^/.]+)/remove')
    def remove_project_member(self, request, pk=None, member_id=None):
        """POST /api/v1/projects/{id}/members/{mid}/remove/"""
        project = self.get_object()

        if not has_permission(request.user, project, MEMBER_REMOVE):
            return Response({'error': 'You do not have permission to remove project members'}, status=status.HTTP_403_FORBIDDEN)

        try:
            member = ProjectMember.objects.get(project=project, id=member_id)
        except ProjectMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

        member.delete()
        return Response({'status': 'removed'})

    @action(detail=True, methods=['post'], url_path=r'members/(?P<member_id>[^/.]+)/change-role')
    def change_project_member_role(self, request, pk=None, member_id=None):
        """POST /api/v1/projects/{id}/members/{mid}/change-role/"""
        project = self.get_object()

        if not has_permission(request.user, project, MEMBER_ROLE_CHANGE):
            return Response({'error': 'You do not have permission to change project member roles'}, status=status.HTTP_403_FORBIDDEN)

        try:
            member = ProjectMember.objects.get(project=project, id=member_id)
        except ProjectMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

        new_role = request.data.get('role')
        expires_at = request.data.get('expires_at')
        permissions_override = request.data.get('permissions')

        if new_role and new_role in dict(ProjectMember.Role.choices):
            member.role = new_role
        if expires_at is not None:
            member.expires_at = expires_at if expires_at else None
        if permissions_override is not None:
            member.permissions = permissions_override if isinstance(permissions_override, list) else []

        member.save(update_fields=[f for f in ('role', 'expires_at', 'permissions') if f in request.data])
        return Response({'status': 'updated'})
