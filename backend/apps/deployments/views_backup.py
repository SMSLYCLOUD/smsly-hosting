import logging
logger = logging.getLogger(__name__)
from .views_files import _generate_signed_download_url
from .views_files import _open_backup_download_response
from .views_files import _verify_signed_download
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


class ServiceBackupViewSet(viewsets.ModelViewSet):
    queryset = ServiceBackup.objects.all().order_by('-created_at')
    serializer_class = ServiceBackupSerializer
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _user_can_access_service(user, service):
        if not user or not user.is_authenticated or not service:
            return False
        if user.is_superuser or service.owner_id == user.id:
            return True
        return service.project_id and service.project.team_id and service.project.team.members.filter(user=user).exists()

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        from django.db.models import Q
        qs = self.queryset.filter(
            Q(service__owner=self.request.user) | Q(service__project__team__members__user=self.request.user)
        ).distinct().order_by('-created_at')
        project_id = self.request.query_params.get('project_id')
        if project_id:
            qs = qs.filter(service__project_id=project_id)
        service_pk = self.kwargs.get('service_pk')
        if service_pk:
            qs = qs.filter(service_id=service_pk)
        return qs

    def perform_create(self, serializer):
        service = serializer.validated_data.get('service')
        if not self._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")
        backup = serializer.save(created_by=self.request.user, status='PENDING')
        create_service_backup_task.delay(service_id=str(backup.service.id), backup_type='MANUAL', backup_id=str(backup.id))

    @action(detail=False, methods=['post'], url_path='import-key')
    def import_key(self, request):
        """Register a foreign BACKUP_ENCRYPTION_KEY on this master for
        cross-master restore. Accepts ``key_id`` (8-char hex from the
        source backup's V2 header) and ``key_material`` (the source's
        Fernet ``BACKUP_ENCRYPTION_KEY`` from ``.env``).

        The action is admin-only and audit-logged. The imported key is
        stored encrypted at rest with ``FIELD_ENCRYPTION_KEY`` and is
        only consulted when the V2 header's ``key_id`` does not match
        this master's active key.
        """
        from .services.backup_service import (
            BackupKeyCollisionError,
            BackupService,
        )
        if not request.user.is_superuser:
            return Response(
                {'error': 'Admin only. Use the install.sh on each master to manage keys.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        key_id = str(request.data.get('key_id') or '').strip()
        key_material = str(request.data.get('key_material') or '').strip()
        label = str(request.data.get('label') or '').strip()[:100]
        if not key_id or not key_material:
            return Response(
                {'error': 'Both "key_id" and "key_material" are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = BackupService.import_backup_key(
                key_id=key_id,
                key_material=key_material,
                label=label,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except BackupKeyCollisionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        try:
            AuditLog(
                actor=request.user.get_username(),
                action='BACKUP_KEY_IMPORTED' if result.get('source') == 'IMPORTED' else 'BACKUP_KEY_REIMPORTED',
                target=f'key_id={result["key_id"]}',
                metadata={
                    'fingerprint': result['fingerprint'],
                    'label': label,
                    'created': result.get('created', False),
                },
            ).save()
        except Exception:
            pass
        return Response(result, status=status.HTTP_201_CREATED if result.get('created') else status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='header')
    def header(self, request, pk=None):
        """Return the V2 backup header (key_id, fingerprint) so the
        operator can copy the key_id to a different master for the
        ``import-key`` flow. Returns 404 if the backup is not in V2
        format.
        """
        from .services.backup_service import BackupService
        backup = self.get_object()
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(info)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        backup = self.get_object()

        # Enforce explicit confirmation for destructive actions
        confirm = request.data.get('confirm')
        if str(confirm).lower() != 'true':
            return Response(
                {'error': 'Explicit confirmation required. Send "confirm": true.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        target_service_id = request.data.get('target_service_id')

        if target_service_id:
            target_service = Service.objects.filter(
                id=target_service_id,
            ).select_related('project__team').first()
            if not self._user_can_access_service(request.user, target_service):
                return Response(
                    {'error': 'Target service not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            target_service = backup.service

        guard = ServerGuard.check_user_workload_allowed(getattr(target_service, 'server', None))
        if not guard["ok"]:
            return Response(guard, status=status.HTTP_400_BAD_REQUEST)

        # ── Pre-flight safety snapshot check (Fix 4) ─────────────
        # Run a synchronous PRE_TRANSFER snapshot. If it fails, return 422
        # with the snapshot error so the API consumer sees the failure
        # instead of silently losing data on a corrupt restore.
        try:
            from .services.backup_service import BackupService
            BackupService().backup_service(
                target_service.id, backup_type='PRE_TRANSFER',
            )
        except Exception as snap_exc:
            logger.warning(
                "Pre-restore snapshot failed for service %s: %s",
                target_service.id, snap_exc,
            )
            return Response(
                {
                    'error': (
                        'Pre-restore safety snapshot failed. Refusing to '
                        'restore to avoid data loss on a corrupt restore.'
                    ),
                    'snapshot_error': str(snap_exc),
                    'backup_id': str(backup.id),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        restore_service_backup_task.delay(
            backup_id=str(backup.id),
            target_service_id=str(target_service_id) if target_service_id else None,
            requesting_user_id=request.user.id,
            raise_on_snapshot_failure=True,
        )
        return Response({'status': 'restore_started'})

    @action(detail=True, methods=['get'], permission_classes=[permissions.AllowAny], authentication_classes=[])
    def download(self, request, pk=None):
        signed_value = request.query_params.get('signed')
        token_value = request.query_params.get('token')
        if token_value:
            return Response({'error': 'Raw token auth is disabled; use a signed download link.'}, status=status.HTTP_401_UNAUTHORIZED)
        if signed_value:
            if not _verify_signed_download(signed_value, str(pk)):
                return Response({'error': 'Invalid or expired download link'}, status=status.HTTP_401_UNAUTHORIZED)
        elif not request.user.is_authenticated:
            return Response({'error': 'Authentication credentials were not provided.'}, status=status.HTTP_401_UNAUTHORIZED)

        # Bypass get_queryset() which filters by request.user — signed/AllowAny
        # requests have an AnonymousUser that crashes the queryset filter.
        backup = self.queryset.model.objects.filter(pk=pk).first()
        if not backup:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        file_path = backup.file_path

        if not file_path or not os.path.exists(file_path):
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)

        from .services.backup_service import BackupService, UnknownBackupKeyIdError
        key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()

        # If the file is encrypted, we must decrypt it for the user to download
        if file_path.endswith('.enc') and key:
            try:
                decrypted_path = BackupService.decrypt_backup(file_path, key)
                return _open_backup_download_response(
                    request,
                    decrypted_path,
                    os.path.basename(file_path).replace(".enc", ""),
                    cleanup_path=decrypted_path,
                )
            except UnknownBackupKeyIdError as exc:
                return Response(
                    {
                        'error': str(exc),
                        'key_id': exc.key_id,
                        'fingerprint': exc.fingerprint,
                        'remediation': (
                            'POST /api/v1/backups/service/import-key/ with '
                            'key_id and key_material from the source master, '
                            'then retry this download.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to decrypt backup for download: {e}")
                return Response({'error': 'Failed to decrypt backup for download.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return _open_backup_download_response(
            request,
            file_path,
            os.path.basename(file_path),
        )

    @action(detail=True, methods=['get'], url_path='download-url')
    def download_url(self, request, pk=None):
        backup = self.get_object()
        return Response({'url': _generate_signed_download_url(request, str(backup.id), 'backup-download', path_params={'pk': str(backup.id)})})


class ServerBackupViewSet(viewsets.ModelViewSet):
    serializer_class = ServerBackupSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = ServerBackup.objects.all().order_by('-created_at')

    def perform_create(self, serializer):
        backup = serializer.save(status='PENDING')
        create_server_backup_task.delay(backup_id=str(backup.id))

    @action(detail=False, methods=['post'], url_path='import-key')
    def import_key(self, request):
        """Server-wide counterpart of :meth:`ServiceBackupViewSet.import_key`.
        Same admin-only + audit-logged + cross-master restore flow.
        """
        from .services.backup_service import (
            BackupKeyCollisionError,
            BackupService,
        )
        if not request.user.is_superuser:
            return Response(
                {'error': 'Admin only.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        key_id = str(request.data.get('key_id') or '').strip()
        key_material = str(request.data.get('key_material') or '').strip()
        label = str(request.data.get('label') or '').strip()[:100]
        if not key_id or not key_material:
            return Response(
                {'error': 'Both "key_id" and "key_material" are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = BackupService.import_backup_key(
                key_id=key_id,
                key_material=key_material,
                label=label,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except BackupKeyCollisionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        try:
            AuditLog(
                actor=request.user.get_username(),
                action='BACKUP_KEY_IMPORTED' if result.get('source') == 'IMPORTED' else 'BACKUP_KEY_REIMPORTED',
                target=f'key_id={result["key_id"]}',
                metadata={
                    'fingerprint': result['fingerprint'],
                    'label': label,
                    'created': result.get('created', False),
                },
            ).save()
        except Exception:
            pass
        return Response(result, status=status.HTTP_201_CREATED if result.get('created') else status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='header')
    def header(self, request, pk=None):
        """Server-wide counterpart of :meth:`ServiceBackupViewSet.header`."""
        from .services.backup_service import BackupService
        backup = self.get_object()
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(info)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        backup = self.get_object()

        # Enforce explicit confirmation for destructive actions
        confirm = request.data.get('confirm')
        if str(confirm).lower() != 'true':
            return Response(
                {'error': 'Explicit confirmation required. Send "confirm": true.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if backup.status != 'COMPLETED':
            return Response({'error': 'Only COMPLETED backups can be restored.'}, status=status.HTTP_400_BAD_REQUEST)
        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(backup_id=str(backup.id))
        return Response({
            'status': 'restored',
            'warning': (
                'Database dump was not restored. Manual psql restore required. '
                'See docs/DISASTER_RECOVERY.md for the procedure.'
            ),
        })

    @action(detail=True, methods=['get'], permission_classes=[permissions.AllowAny], authentication_classes=[])
    def download(self, request, pk=None):
        signed_value = request.query_params.get('signed')
        token_value = request.query_params.get('token')
        if token_value:
            return Response({'error': 'Raw token auth is disabled; use a signed download link.'}, status=status.HTTP_401_UNAUTHORIZED)
        if signed_value:
            if not _verify_signed_download(signed_value, str(pk)):
                return Response({'error': 'Invalid or expired download link'}, status=status.HTTP_401_UNAUTHORIZED)
        elif not request.user.is_authenticated:
            return Response({'error': 'Authentication credentials were not provided.'}, status=status.HTTP_401_UNAUTHORIZED)

        # Bypass get_queryset() which filters by request.user — signed/AllowAny
        # requests have an AnonymousUser that crashes the queryset filter.
        backup = self.queryset.model.objects.filter(pk=pk).first()
        if not backup:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        file_path = backup.file_path

        if not file_path or not os.path.exists(file_path):
            return Response({'error': 'Backup file not found on disk.'}, status=status.HTTP_404_NOT_FOUND)

        from .services.backup_service import BackupService, UnknownBackupKeyIdError
        key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()

        # If the file is encrypted, we must decrypt it for the user to download
        if file_path.endswith('.enc') and key:
            try:
                decrypted_path = BackupService.decrypt_backup(file_path, key)

                return _open_backup_download_response(
                    request,
                    decrypted_path,
                    os.path.basename(file_path).replace(".enc", ""),
                    cleanup_path=decrypted_path,
                )
            except UnknownBackupKeyIdError as exc:
                return Response(
                    {
                        'error': str(exc),
                        'key_id': exc.key_id,
                        'fingerprint': exc.fingerprint,
                        'remediation': (
                            'POST /api/v1/backups/server/import-key/ with '
                            'key_id and key_material from the source master, '
                            'then retry this download.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to decrypt backup for download: {e}")
                return Response({'error': 'Failed to decrypt backup for download.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return _open_backup_download_response(
            request,
            file_path,
            os.path.basename(file_path),
        )

    @action(detail=True, methods=['get'], url_path='download-url')
    def download_url(self, request, pk=None):
        backup = self.get_object()
        return Response({'url': _generate_signed_download_url(request, str(backup.id), 'server-backup-download', path_params={'pk': str(backup.id)})})

    @action(detail=False, methods=['post'], url_path='upload-restore',
            parser_classes=[parsers.MultiPartParser])
    def upload_restore(self, request):
        """Accept a backup .tar.gz file upload and restore from it."""
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'error': 'No file uploaded. Send a .tar.gz file as "file".'}, status=status.HTTP_400_BAD_REQUEST)

        if not uploaded.name.endswith(('.tar.gz', '.tgz')):
            return Response({'error': 'File must be a .tar.gz or .tgz archive.'}, status=status.HTTP_400_BAD_REQUEST)

        import uuid as _uuid
        from apps.deployments.services.backup_service import BackupService

        # Save uploaded file to shared backup volume
        backups_dir = BackupService._get_backups_dir('server')
        dest_filename = f"uploaded_restore_{_uuid.uuid4().hex[:8]}.tar.gz"
        dest_path = os.path.join(backups_dir, dest_filename)

        with open(dest_path, 'wb') as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        file_size = os.path.getsize(dest_path)

        # Create a ServerBackup record pointing to the uploaded file
        backup = ServerBackup.objects.create(
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            error_message=f'Uploaded restore from: {uploaded.name}',
        )

        # Trigger async restore
        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(backup_id=str(backup.id), requesting_user_id=request.user.id)

        return Response({
            'status': 'Restore started from uploaded backup.',
            'backup_id': str(backup.id),
            'file_size': file_size,
        })


class BackupScheduleViewSet(viewsets.ModelViewSet):
    queryset = BackupSchedule.objects.all().order_by('id')
    serializer_class = BackupScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def get_queryset(self):
        qs = self.queryset
        if not self.request.user.is_superuser:
            qs = qs.filter(
                Q(service__owner=self.request.user) |
                Q(service__project__team__members__user=self.request.user)
            ).distinct()
        service_id = self.request.query_params.get('service')
        if service_id:
            qs = qs.filter(service_id=service_id)
        return qs

    def _validate_schedule_access(self, serializer):
        service = serializer.validated_data.get(
            'service',
            getattr(serializer.instance, 'service', None),
        )
        is_server_wide = serializer.validated_data.get(
            'is_server_wide',
            getattr(serializer.instance, 'is_server_wide', False),
        )
        if is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can manage server-wide backup schedules.")
        if not service and not is_server_wide:
            raise PermissionDenied("A service is required for non-server-wide backup schedules.")
        if service and not ServiceBackupViewSet._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")

    def perform_create(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()
