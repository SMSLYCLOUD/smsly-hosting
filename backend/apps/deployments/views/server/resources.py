"""
Service, deployment, domain, and registry mixins for ManagedServerViewSet.
"""

import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models.core import Deployment, Service
from ...serializers import DeploymentSerializer, ServiceSerializer
from .helpers import (
    _extract_page_results_and_next,
    _fetch_remote_json_with_fallback,
    _normalize_remote_api_path,
    _safe_remote_error_payload,
)

logger = logging.getLogger(__name__)


class ResourcesMixin:

    @action(detail=True, methods=["get"])
    def services(self, request, pk=None):
        server = self.get_object()

        if server.is_lite_agent:
            qs = Service.objects.filter(
                server=server,
            ).exclude(
                status=Service.Status.DELETED,
            ).select_related('project').prefetch_related('env_vars').order_by('-updated_at')
            serializer = ServiceSerializer(qs, many=True, context={'request': request})
            return Response({'results': serializer.data, 'count': len(serializer.data)})

        if not server.api_url:
            return Response(_safe_remote_error_payload("services", "Server has no API URL yet."))

        api_path = "/api/v1/services/"
        payload, error_payload = _fetch_remote_json_with_fallback(
            server, "services", api_path, timeout=15
        )
        if error_payload:
            return Response(error_payload)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def deployments(self, request, pk=None):
        server = self.get_object()

        if server.is_lite_agent:
            qs = Deployment.objects.filter(
                service__server=server,
            ).select_related('service').order_by('-created_at')[:50]
            serializer = DeploymentSerializer(qs, many=True)
            return Response({'results': serializer.data, 'count': len(serializer.data)})

        if not server.api_url:
            return Response(_safe_remote_error_payload("deployments", "Server has no API URL yet."))

        api_path = "/api/v1/deployments/"
        payload, error_payload = _fetch_remote_json_with_fallback(
            server, "deployments", api_path, timeout=15
        )
        if error_payload:
            return Response(error_payload)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def domains(self, request, pk=None):
        server = self.get_object()

        if server.is_lite_agent:
            services_qs = Service.objects.filter(
                server=server,
            ).exclude(
                status=Service.Status.DELETED,
            ).only('id', 'name', 'public_domain', 'custom_domains', 'domain_verified', 'verification_token')

            domains = []
            for svc in services_qs:
                custom = svc.custom_domains if isinstance(svc.custom_domains, list) else []
                for domain in custom:
                    domains.append({
                        "domain": domain,
                        "service_id": str(svc.id),
                        "service_name": svc.name,
                        "public_domain": svc.public_domain or "",
                        "verified": svc.domain_verified,
                        "verification_token": svc.verification_token or "",
                    })
            return Response({"domains": domains, "count": len(domains)})

        if not server.api_url:
            return Response(_safe_remote_error_payload("domains", "Server has no API URL yet."))

        all_services = []
        seen_paths = set()
        next_path = "/api/v1/services/"
        max_pages = 50

        for _ in range(max_pages):
            normalized_path = _normalize_remote_api_path(next_path)
            if not normalized_path or normalized_path in seen_paths:
                break
            seen_paths.add(normalized_path)

            payload, error_payload = _fetch_remote_json_with_fallback(
                server, "domains", normalized_path, timeout=15
            )
            if error_payload:
                if not all_services:
                    return Response(error_payload)
                break

            services_page, next_link = _extract_page_results_and_next(payload)
            all_services.extend(services_page)
            if not next_link:
                break
            next_path = _normalize_remote_api_path(next_link)

        domains = []
        for svc in all_services:
            svc_id = svc.get("id", "")
            svc_name = svc.get("name", "")
            public_domain = svc.get("public_domain", "")
            custom_domains = svc.get("custom_domains", [])
            if not isinstance(custom_domains, list):
                custom_domains = []
            for domain in custom_domains:
                domains.append({
                    "domain": domain,
                    "service_id": svc_id,
                    "service_name": svc_name,
                    "public_domain": public_domain,
                    "verified": svc.get("domain_verified", False),
                    "verification_token": svc.get("verification_token", ""),
                })

        return Response({"domains": domains, "count": len(domains)})

    @action(detail=True, methods=["get", "post"], url_path="registries")
    def registries(self, request, pk=None):
        server = self.get_object()

        if request.method == "POST":
            registry_ids = request.data.get("registry_ids", [])
            if not isinstance(registry_ids, list):
                return Response(
                    {"error": "registry_ids must be a list of UUIDs"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            from django.contrib.contenttypes.models import ContentType
            from django.db.models import Q

            from apps.organizations.models import Organization, OrganizationMembership
            from apps.teams.models import Team, TeamMember

            from apps.deployments.models import ScopedRegistry
            from apps.deployments.models.core import Project
            from apps.deployments.models.project import ProjectMember

            user_org_ids = set(
                OrganizationMembership.objects
                .filter(user=request.user)
                .values_list("organization_id", flat=True)
            ) | set(
                Organization.objects.filter(owner=request.user).values_list("id", flat=True)
            )
            user_team_ids = set(
                TeamMember.objects
                .filter(user=request.user, is_active=True)
                .values_list("team_id", flat=True)
            )
            user_team_ids |= set(
                Team.objects.filter(owner=request.user).values_list("id", flat=True)
            )
            user_project_ids = set(
                Project.objects.filter(owner=request.user).values_list("id", flat=True)
            ) | set(
                ProjectMember.objects.filter(user=request.user).values_list("project_id", flat=True)
            )

            org_ct = ContentType.objects.get_for_model(Organization)
            team_ct = ContentType.objects.get_for_model(Team)
            project_ct = ContentType.objects.get_for_model(Project)

            accessible_scopes = (
                Q(content_type=org_ct, object_id__in=user_org_ids)
                | Q(content_type=team_ct, object_id__in=user_team_ids)
                | Q(content_type=project_ct, object_id__in=user_project_ids)
            )

            registries = (
                ScopedRegistry.objects
                .filter(id__in=registry_ids, is_active=True)
                .filter(accessible_scopes)
            )
            if len(registries) != len(registry_ids):
                return Response(
                    {"error": "One or more registry IDs are invalid, inactive, or inaccessible"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            server.registry_access.set(registries)
            logger.info(
                "Set %d registries for server %s (%s)",
                len(registries), server.name, server.id,
            )
            return Response({
                "registry_ids": [str(r.id) for r in registries],
            })

        registries = server.registry_access.filter(is_active=True)
        return Response({
            "count": registries.count(),
            "registries": [
                {
                    "id": str(r.id),
                    "registry_url": r.registry_url,
                    "is_internal": r.is_internal,
                    "scope_type": r.content_type.model if r.content_type else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in registries
            ],
        })
