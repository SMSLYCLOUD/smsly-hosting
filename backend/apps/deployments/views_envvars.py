import re
import logging
logger = logging.getLogger(__name__)
_ENV_KEY_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_MASKED_SECRET_PATTERN = re.compile(r'^[\*\u2022]{4,}$')
_MASKED_SECRET_PATTERN = re.compile(r'^[\*\u2022]{4,}$')


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


def _is_valid_env_key(key: str) -> bool:
    """Return True when an env var key is in shell-safe format."""
    return bool(_ENV_KEY_PATTERN.match(str(key or '').strip()))


def _looks_masked_secret(value: str) -> bool:
    """
    Detect masked secret placeholders from UI payloads.
    Accepts repeated asterisks or bullet characters.
    """
    return bool(_MASKED_SECRET_PATTERN.match(str(value or '').strip()))


class ServiceEnvVarActionsMixin:
    @action(detail=True, methods=['get', 'post'], url_path='env_vars')
    def env_vars(self, request, pk=None):
        service = self.get_object()
        reveal_secrets = not hasattr(getattr(request, 'auth', None), 'prefix')

        def _is_ciphertext(val: str) -> bool:
            """Detect Fernet ciphertext to prevent storing it as plaintext."""
            if not val or not isinstance(val, str):
                return False
            if val.startswith("gAAAA"):
                return True
            if len(val) > 100 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in val):
                try:
                    import base64
                    padded = val + '=' * (-len(val) % 4)
                    decoded = base64.urlsafe_b64decode(padded)
                    if len(decoded) >= 57 and decoded[0] == 0x80:
                        return True
                except Exception:
                    pass
            return False

        if request.method.upper() == 'GET':
            vars = service.env_vars.all().order_by('key')
            serializer = EnvVarSerializer(
                vars,
                many=True,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            )
            return Response(serializer.data)

        assert_can_write(self.request.user, service)
        payload_vars = request.data.get('vars')
        if payload_vars is not None:
            if not isinstance(payload_vars, list):
                return Response(
                    {'error': '"vars" must be a list of objects.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            normalized = []
            seen_keys = set()
            skipped_count = 0

            for idx, row in enumerate(payload_vars):
                if not isinstance(row, dict):
                    return Response(
                        {'error': f'Invalid item at index {idx}; expected object.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                key = str(row.get('key') or '').strip()
                if not key:
                    return Response(
                        {'error': f'Missing key at index {idx}.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not _is_valid_env_key(key):
                    return Response(
                        {'error': f'Invalid environment variable key "{key}".'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if key in seen_keys:
                    return Response(
                        {'error': f'Duplicate key "{key}" in import payload.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                seen_keys.add(key)

                existing = EnvironmentVariable.objects.filter(
                    service=service, key=key).first()
                value = str(row.get('value', '') or '')
                if existing and existing.is_secret and _looks_masked_secret(value):
                    value = existing.value

                if _is_ciphertext(value):
                    logger.warning(
                        "[DB-ENCRYPT] Rejecting ciphertext env var %s for service %s — "
                        "sender sent undecrypted/double-encrypted data. "
                        "This var will NOT be saved to prevent corruption.",
                        key, service.name,
                    )
                    skipped_count += 1
                    continue

                if 'is_secret' in row:
                    is_secret = _parse_bool(row.get('is_secret'))
                else:
                    is_secret = bool(existing.is_secret) if existing else False

                normalized.append({
                    'key': key,
                    'value': value,
                    'is_secret': is_secret,
                })

            added = 0
            updated = 0
            try:
                with transaction.atomic():
                    for item in normalized:
                        _, created = EnvironmentVariable.objects.update_or_create(
                            service=service,
                            key=item['key'],
                            defaults={
                                'value': item['value'],
                                'is_secret': item['is_secret'],
                                'source': 'USER',
                            },
                        )
                        if created:
                            added += 1
                        else:
                            updated += 1
            except (ValidationError, DataError, IntegrityError) as exc:
                logger.warning(
                    "Invalid bulk env payload for service %s: %s",
                    service.id, exc,
                )
                return Response(
                    {'error': 'Invalid environment variable payload.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Failed bulk env upsert for service %s: %s", service.id, exc)
                return Response(
                    {'error': 'Failed to save environment variables'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            serializer = EnvVarSerializer(
                service.env_vars.all().order_by('key'),
                many=True,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            )
            resp_data = {
                'added': added,
                'updated': updated,
                'count': len(normalized),
                'env_vars': serializer.data,
            }
            if skipped_count > 0:
                resp_data['warning'] = f"Skipped {skipped_count} environment variables with ciphertext values."
            return Response(resp_data)

        # Allow partial data — key is required, value can be empty
        key = str(request.data.get('key') or '').strip()
        if not key:
            return Response(
                {'key': ['This field is required.']},
                status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_env_key(key):
            return Response(
                {'key': ['Use letters, numbers, and underscore; cannot start with a number.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = EnvironmentVariable.objects.filter(service=service, key=key).first()
        value = str(request.data.get('value', '') or '')
        if existing and existing.is_secret and _looks_masked_secret(value):
            value = existing.value
        if _is_ciphertext(value):
            return Response(
                {'value': ['Cannot save Fernet ciphertext as value. Sender must decrypt before sending.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'is_secret' in request.data:
            is_secret = _parse_bool(request.data.get('is_secret'))
        else:
            is_secret = bool(existing.is_secret) if existing else False

        is_locked = _parse_bool(request.data.get('is_locked', False))

        try:
            env_var, created = EnvironmentVariable.objects.update_or_create(
                service=service,
                key=key,
                defaults={'value': value, 'is_secret': is_secret, 'is_locked': is_locked, 'source': 'USER'},
            )
        except (ValidationError, DataError, IntegrityError) as exc:
            logger.warning("Invalid env var payload for service %s key=%s: %s", service.id, key, exc)
            return Response(
                {'error': f'Invalid environment variable payload for key "{key}"'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to save env var for service %s key=%s: %s", service.id, key, exc)
            return Response(
                {'error': 'Failed to save environment variable'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        out = EnvVarSerializer(
            env_var,
            context={'request': request, 'reveal_secrets': reveal_secrets},
        ).data
        return Response(
            out,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


    @action(detail=True, methods=['get', 'delete', 'patch'],
            url_path='env_vars/(?P<var_id>\\d+)')
    def env_var_detail(self, request, pk=None, var_id=None):
        """GET / PATCH / DELETE on a single env var.

        The frontend ``getEnvVarValue`` (api.ts:591) calls
        ``GET /services/{id}/env_vars/{varId}/`` to reveal a
        secret. The previous decorator only allowed
        ``['delete', 'patch']`` which made the GET return 405
        and the secret-reveal flow silently fail.
        """
        service = self.get_object()
        try:
            var = EnvironmentVariable.objects.get(id=var_id, service=service)
        except EnvironmentVariable.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if request.method.upper() == 'GET':
            reveal_secrets = (
                request.user.is_superuser
                or var.service.owner_id == request.user.id
                or getattr(request, 'auth', None)
                and hasattr(request.auth, 'prefix')  # APIToken
            )
            return Response(
                EnvVarSerializer(
                    var,
                    context={'request': request, 'reveal_secrets': reveal_secrets},
                ).data
            )
        assert_can_write(self.request.user, service)
        if request.method.upper() == 'DELETE':
            var.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH — toggle is_locked (or update any field)
        if 'is_locked' in request.data:
            var.is_locked = _parse_bool(request.data['is_locked'])
        if 'is_secret' in request.data:
            var.is_secret = _parse_bool(request.data['is_secret'])
        var.save()
        return Response(
            EnvVarSerializer(
                var,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            ).data
        )
