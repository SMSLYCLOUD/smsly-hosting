import logging
logger = logging.getLogger(__name__)
from .views_domains import _parse_bool
import os
import posixpath
import hmac
import re
from rest_framework import viewsets, permissions, status, parsers, serializers, authentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.utils import timezone
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.db.models import Prefetch
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction, models
from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField
from django.utils.http import content_disposition_header
from django.core import signing
from apps.deployments.services.github_webhooks import setup_github_webhook
from apps.deployments.services.gitlab_webhooks import setup_gitlab_webhook
from apps.deployments.services.bitbucket_webhooks import setup_bitbucket_webhook
import threading
from .ai_router import DEFAULT_AI_ROUTER_API_BASE, DEFAULT_AI_ROUTER_UI_BASE, DEFAULT_BRAID_ALIAS, is_ai_router_service, persist_ai_router_config, serialize_ai_router_config
from .models import Service, Deployment, EnvironmentVariable, PlatformConfig
from .serializers import ServiceSerializer, DeploymentSerializer, DeploymentTriggerSerializer, EnvVarSerializer, DeploymentTimelineSerializer, InstantRollbackSerializer, AuditLogSerializer, DeploymentApproveSerializer, ServiceBackupSerializer, ServerBackupSerializer, BackupScheduleSerializer
from .models_audit import AuditLog
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .tasks import smart_deploy_task, resume_deploy_task, create_service_backup_task, create_server_backup_task, restore_service_backup_task, enqueue_smart_deploy_task
from .rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from .domain_utils import normalize_domain
from .services.server_guard import ServerGuard
from apps.cloud.models import CloudProvider
import uuid
import logging
import re
from celery.result import AsyncResult
from apps.cloud.docker_client import get_docker_client
from .utils import validate_and_sanitize_path
from apps.deployments.utils import resolve_running_container
from apps.teams.permissions import get_team_q_filter, assert_can_write, assert_can_delete, user_can_read
from .views_audit import AuditLogViewSet
from .views_auth import SessionTokenView
from .views_route_status import RouteStatusView
from .views_transfer import ServerTransferViewSet


class ServiceAIRouterActionsMixin:
    @action(detail=True, methods=['get', 'post'], url_path='ai-router-config')
    def ai_router_config(self, request, pk=None):
        service = self.get_object()
        if not is_ai_router_service(service):
            return Response(
                {'error': 'This service is not an AI Router.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.method.upper() == 'GET':
            return Response(serialize_ai_router_config(service))

        raw_ids = request.data.get('selected_service_ids', [])
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list):
            return Response(
                {'error': '"selected_service_ids" must be a list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_base = str(
            request.data.get('api_base', DEFAULT_AI_ROUTER_API_BASE) or DEFAULT_AI_ROUTER_API_BASE
        ).strip() or DEFAULT_AI_ROUTER_API_BASE
        if not api_base.startswith('/'):
            api_base = f'/{api_base}'

        ui_base = str(
            request.data.get('ui_base', DEFAULT_AI_ROUTER_UI_BASE) or DEFAULT_AI_ROUTER_UI_BASE
        ).strip() or DEFAULT_AI_ROUTER_UI_BASE
        if not ui_base.startswith('/'):
            ui_base = f'/{ui_base}'

        braid_alias = str(
            request.data.get('braid_alias', DEFAULT_BRAID_ALIAS) or DEFAULT_BRAID_ALIAS
        ).strip() or DEFAULT_BRAID_ALIAS
        braid_enabled = _parse_bool(request.data.get('braid_enabled', True))

        persist_ai_router_config(
            service,
            selected_service_ids=[str(item).strip() for item in raw_ids],
            api_base=api_base,
            ui_base=ui_base,
            braid_alias=braid_alias,
            braid_enabled=braid_enabled,
        )
        service.refresh_from_db()
        return Response(serialize_ai_router_config(service))
