"""Views Templates module."""
import json
import os
import re
import secrets

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.deployments.services.ai_router import (
    DEFAULT_AI_ROUTER_API_BASE,
    DEFAULT_AI_ROUTER_UI_BASE,
    DEFAULT_BRAID_ALIAS,
)


class TemplateSchemaSerializer(serializers.Serializer):
    """Schema placeholder for template endpoints."""


class TemplateViewSet(viewsets.GenericViewSet):
    """
    Returns a list of predefined application templates.
    """
    serializer_class = TemplateSchemaSerializer
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id='templates_list',
        responses=OpenApiTypes.OBJECT,
    )
    def list(self, request):
        # Load from fixtures
        try:
            path = os.path.join(settings.BASE_DIR,
                                'apps/deployments/fixtures/templates.json')
            with open(path, encoding='utf-8-sig') as f:
                data = json.load(f)

            category = request.query_params.get('category')
            search = request.query_params.get('search')

            if category:
                data = [t for t in data if t.get('category') == category]

            if search:
                search = search.lower()
                data = [
                    t for t in data if search in t.get(
                        'name', '').lower() or search in t.get(
                        'description', '').lower()
                ]

            return Response(data)
        except Exception as e:
            return Response({'error': str(e)},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @extend_schema(
        operation_id='templates_retrieve_by_id',
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
            )
        ],
        responses=OpenApiTypes.OBJECT,
    )
    def retrieve(self, request, pk=None):
        try:
            path = os.path.join(settings.BASE_DIR,
                                'apps/deployments/fixtures/templates.json')
            with open(path, encoding='utf-8-sig') as f:
                data = json.load(f)

            template = next((t for t in data if t['id'] == pk), None)
            if template:
                return Response(template)
            return Response(status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
            )
        ],
        responses=OpenApiTypes.OBJECT,
    )
    def one_click_deploy(self, request, pk=None):
        """
        One-click deploy a template.

        Creates a Service + Env Vars immediately, then queues a background task to:
        - provision required addons (if any)
        - trigger the deployment
        """
        try:
            path = os.path.join(settings.BASE_DIR,
                                'apps/deployments/fixtures/templates.json')
            with open(path, encoding='utf-8-sig') as f:
                data = json.load(f)

            template = next((t for t in data if t['id'] == pk), None)
            if not template:
                return Response({'error': 'Template not found'},
                                status=status.HTTP_404_NOT_FOUND)

            # Check system requirements before proceeding
            from apps.deployments.utils.resources import check_requirements
            min_ram = template.get('min_ram_gb')
            min_cpu = template.get('min_cpu_cores')
            min_disk = template.get('min_disk_gb')
            gpu_req = template.get('gpu_required', False)

            success, error_msg = check_requirements(
                min_ram_gb=min_ram,
                min_cpu_cores=min_cpu,
                min_disk_gb=min_disk,
                gpu_required=gpu_req
            )
            if not success:
                return Response({
                    'error': 'System requirements not met',
                    'detail': error_msg,
                    'requirements': {
                        'min_ram_gb': min_ram,
                        'min_cpu_cores': min_cpu,
                        'min_disk_gb': min_disk,
                        'gpu_required': gpu_req
                    }
                }, status=status.HTTP_400_BAD_REQUEST)

            docker_image = template.get('docker_image')
            if not docker_image:
                return Response({'error': 'Template is missing docker_image'},
                                status=status.HTTP_400_BAD_REQUEST)

            from apps.cloud.models import CloudProvider
            from apps.deployments.models import EnvironmentVariable, Service
            from apps.deployments.tasks.deployment.tasks_templates import one_click_deploy_template_task

            # Resolve provider (prefer existing active provider, else create LOCAL fallback).
            provider = CloudProvider.objects.filter(is_active=True).first()
            if not provider:
                provider = CloudProvider.objects.create(
                    name='Local Docker',
                    provider_type=CloudProvider.ProviderType.LOCAL,
                    is_active=True,
                )

            def slugify(value: str) -> str:
                value = (value or 'service').lower()
                value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')
                return (value[:48] or 'service')

            suffix = secrets.token_hex(4)
            base = slugify(str(template.get('id') or template.get('name') or 'service'))
            name = f"{base}-{suffix}"[:63]

            internal_port = int(template.get('default_port') or 8000)

            # Decide whether the deployed Service should be exposed on a
            # public Caddy route. The template fixture's supports_public_url
            # flag is the source of truth: HTTP-speaking services (frontend,
            # CMS, dev-tools) keep a public route; non-HTTP services
            # (databases, caches, brokers — redis on RESP, postgres on
            # SQL wire, etc.) must NOT get a public route. Otherwise
            # Caddy reverse-proxies HTTP to a service that doesn't speak
            # HTTP on its default port, the upstream closes / hangs, and
            # Caddy returns 503.
            #
            # Service.save() auto-generates a public_domain if one is not
            # set (see models_core.Service.save). The way to suppress that
            # auto-generation and keep the service internal-only is to set
            # public_domain_hidden=True after create() — caddy_manager
            # already treats that flag as "skip public route".
            supports_public_url = bool(template.get('supports_public_url', False))
            service = Service.objects.create(
                name=name,
                deploy_type='DOCKER',
                docker_image=str(docker_image),
                internal_port=internal_port,
                owner=request.user,
                provider=provider,
                public_domain_hidden=(not supports_public_url),
            )

            getattr(settings, 'DOMAIN', 'localhost') or 'localhost'
            service_domain = service.public_domain

            def render_value(raw: str) -> str:
                v = str(raw or '')
                v = v.replace('${RANDOM_PASSWORD}', secrets.token_urlsafe(24))
                v = v.replace('${DOMAIN}', service_domain)
                v = v.replace('${AI_SENATE_URL}', os.environ.get('AI_SENATE_URL', 'https://senate.smsly.cloud'))
                v = v.replace('${AI_ROUTER_API_BASE}', os.environ.get('AI_ROUTER_API_BASE', DEFAULT_AI_ROUTER_API_BASE))
                v = v.replace('${AI_ROUTER_UI_BASE}', os.environ.get('AI_ROUTER_UI_BASE', DEFAULT_AI_ROUTER_UI_BASE))
                v = v.replace('${AI_ROUTER_BRAID_ALIAS}', os.environ.get('AI_ROUTER_BRAID_ALIAS', DEFAULT_BRAID_ALIAS))
                return v

            # Seed env vars from the fixture (if any).
            env_vars = template.get('env_vars') or []
            if isinstance(env_vars, list):
                for item in env_vars:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get('key') or '').strip()
                    if not key:
                        continue
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key=key,
                        defaults={
                            'value': render_value(item.get('value', '')),
                            'is_secret': bool(item.get('is_secret', False)),
                        }
                    )

            # Ensure PORT and PUBLIC_DOMAIN are always present for local deployments / routing.
            EnvironmentVariable.objects.update_or_create(
                service=service,
                key='PORT',
                defaults={'value': str(internal_port), 'is_secret': False}
            )
            EnvironmentVariable.objects.update_or_create(
                service=service,
                key='PUBLIC_DOMAIN',
                defaults={'value': service_domain, 'is_secret': False}
            )


            # Queue background orchestration (addons + deploy)
            # We pass the template ID so the task can handle complex logic like addon provisioning.
            async_result = one_click_deploy_template_task.delay(str(service.id), str(template['id']))

            return Response({
                'service_id': str(service.id),
                'service_name': service.name,
                'task_id': async_result.id,
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({'error': str(e)},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
