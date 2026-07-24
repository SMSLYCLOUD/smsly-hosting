"""server_backup views."""
import logging
import os

logger = logging.getLogger(__name__)



import contextlib
from django.http import HttpResponse
from rest_framework import parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from ..models.backup import ServerBackup
from ..models.audit import AuditLog
from ..serializers import ServerBackupSerializer
from ..tasks import create_server_backup_task
from ._helpers import (
    _generate_signed_download_url,
    _open_backup_download_response,
    _resolve_encryption_key,
    _verify_signed_download,
)
class ServerBackupViewSet(viewsets.ModelViewSet):
    serializer_class = ServerBackupSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = ServerBackup.objects.all().order_by('-created_at')

    def perform_create(self, serializer):
        backup = serializer.save(status='PENDING')
        create_server_backup_task.delay(backup_id=str(backup.id))

    @action(detail=True, methods=['get'], url_path='download-key', permission_classes=[permissions.AllowAny], authentication_classes=[])
    def download_key(self, request, pk=None):
        """Download the V2 backup header as a .key.json file. See
        ServiceBackupViewSet.download_key for details. Public for the
        same reason — key_id/fingerprint are not secret material.
        """
        import json as _json


        from ..services.backup_service import BackupService
        backup = self.get_object()
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        key_payload = {
            'backup_id': str(backup.id),
            'scope': 'server',
            'created_at': backup.created_at.isoformat() if backup.created_at else None,
            'encryption': {
                'format': info.get('format', 'CHUNKED_V2'),
                'key_id': info.get('key_id'),
                'fingerprint': info.get('fingerprint'),
            },
            'usage': (
                'Import this key on the target master with: '
                'POST /api/v1/backups/import-key/ '
                '{"key_id": "<key_id>", "key_material": "<source BACKUP_ENCRYPTION_KEY>"}'
            ),
        }
        response = HttpResponse(
            _json.dumps(key_payload, indent=2),
            content_type='application/json',
        )
        response['Content-Disposition'] = f'attachment; filename="backup-{backup.id}-key.json"'
        return response

    @action(detail=False, methods=['post'], url_path='import-key')
    def import_key(self, request):
        """Server-wide counterpart of :meth:`ServiceBackupViewSet.import_key`.
        Same admin-only + audit-logged + cross-master restore flow.
        """
        from ..services.backup_service import (
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
        with contextlib.suppress(Exception):
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
        return Response(result, status=status.HTTP_201_CREATED if result.get('created') else status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='header')
    def header(self, request, pk=None):
        """Server-wide counterpart of :meth:`ServiceBackupViewSet.header`."""
        from ..services.backup_service import BackupService
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

        # Pre-flight: verify encryption key is available for cross-master restores
        key_provided = _resolve_encryption_key(request)
        if backup.file_path and backup.file_path.endswith('.enc'):
            from ..services.backup_service import (
                BackupService,
            )
            if not BackupService.can_decrypt_backup(backup.file_path, passed_key=key_provided):
                try:
                    header = BackupService.read_v2_header(backup.file_path)
                    header_key_id = header.get('key_id', 'unknown')
                    return Response(
                        {
                            'error': (
                                'Encryption key required. This backup '
                                'was encrypted on a different master. '
                                'Import the key or provide it in the request.'
                            ),
                            'error_code': 'ENCRYPTION_KEY_REQUIRED',
                            'key_id': header_key_id,
                            'remediation': (
                                'POST /api/v1/server/backups/import-key/ with '
                                'key_id and key_material from the source master, '
                                'or send "encryption_key" in the restore request body, '
                                'or upload a key_file JSON.'
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                except (OSError, ValueError):
                    pass

        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(
            backup_id=str(backup.id),
            encryption_key=key_provided or None,
            requesting_user_id=request.user.id,
        )
        return Response({
            'status': 'restore_started',
            'volume_restore_required': True,
            'volume_warning': (
                "Docker volumes are NOT restored during server restores. "
                "Volumes live on the host filesystem and will be re-attached "
                "on the next deployment of each service. If volume data was "
                "also lost, restore it from service-level backups or filesystem "
                "snapshots."
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
            # File missing locally — try to download from cloud storage
            from ..services.backup_service import _download_backup_from_cloud
            if getattr(backup, 'cloud_uploaded', False):
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                if _download_backup_from_cloud(backup, file_path):
                    logger.info("Downloaded server backup %s from cloud to %s", backup.id, file_path)
                else:
                    return Response({'error': 'Backup file not found on disk and cloud download failed.'}, status=status.HTTP_404_NOT_FOUND)
            else:
                return Response({'error': 'Backup file not found on disk.'}, status=status.HTTP_404_NOT_FOUND)

        from ..services.backup_service import BackupService, UnknownBackupKeyIdError
        key = BackupService._get_encryption_key()

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

        # Enforce at-rest encryption policy. If BACKUP_REQUIRE_ENCRYPTION
        # is set, refuse to store an unencrypted uploaded backup.
        svc = BackupService()
        try:
            dest_path = svc._maybe_encrypt(dest_path)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.remove(dest_path)
            return Response(
                {'error': f'Failed to encrypt uploaded backup: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        file_size = os.path.getsize(dest_path)

        # Create a ServerBackup record pointing to the uploaded file
        backup = ServerBackup.objects.create(
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            error_message=f'Uploaded restore from: {uploaded.name}',
        )

        # Accept optional encryption key for cross-master restore
        encryption_key = _resolve_encryption_key(request)

        # Trigger async restore
        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(
            backup_id=str(backup.id),
            encryption_key=encryption_key,
            requesting_user_id=request.user.id,
        )

        return Response({
            'status': 'Restore started from uploaded backup.',
            'backup_id': str(backup.id),
            'file_size': file_size,
        })

    @action(detail=False, methods=['post'], url_path='list-backups')
    def list_cloud_backups(self, request):
        """List available backup files in a cloud storage bucket (server scope)."""
        cloud_storage_id = request.data.get('cloud_storage_id', '').strip()
        prefix = request.data.get('prefix', 'smsly-backups/').strip()

        if not cloud_storage_id:
            return Response(
                {'error': 'cloud_storage_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.deployments.models.cloud_storage import CloudStorageDestination

        try:
            dest = CloudStorageDestination.objects.get(id=cloud_storage_id)
        except CloudStorageDestination.DoesNotExist:
            return Response(
                {'error': 'Cloud storage destination not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Server-level backups must use platform-wide destinations only
        if dest.service is not None:
            return Response(
                {'error': 'Server backups require a platform-wide cloud destination (no service binding).'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from ..services.backup_service import list_s3_objects

        objects = list_s3_objects(
            bucket=dest.bucket,
            prefix=prefix,
            endpoint=dest.endpoint,
            region=dest.region,
            access_key=dest.access_key,
            secret_key=dest.secret_key,
        )

        return Response({'objects': objects, 'bucket': dest.bucket})

    @action(detail=False, methods=['post'], url_path='restore-from-cloud')
    def restore_from_cloud(self, request):
        """Restore a server backup directly from cloud storage."""
        import uuid as _uuid

        from ..services.backup_service import BackupService, download_from_s3, normalize_s3_key

        cloud_storage_id = request.data.get('cloud_storage_id')
        s3_key = request.data.get('s3_key', '').strip()

        if cloud_storage_id:
            from apps.deployments.models.cloud_storage import CloudStorageDestination
            try:
                dest = CloudStorageDestination.objects.get(id=cloud_storage_id)
                # Server-level restores must use platform-wide destinations
                if dest.service is not None:
                    return Response(
                        {'error': 'Server restores require a platform-wide cloud destination (no service binding).'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                s3_bucket = dest.bucket
                endpoint = dest.endpoint
                region = dest.region
                access_key = dest.access_key
                secret_key = dest.secret_key
            except CloudStorageDestination.DoesNotExist:
                return Response({'error': 'Cloud storage destination not found.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            s3_bucket = request.data.get('s3_bucket', '').strip()
            endpoint = request.data.get('s3_endpoint', '').strip()
            region = request.data.get('s3_region', 'us-east-1').strip()
            access_key = request.data.get('s3_access_key', '').strip()
            secret_key = request.data.get('s3_secret_key', '').strip()

        s3_key = normalize_s3_key(s3_key, s3_bucket)

        if not s3_bucket or not s3_key or not access_key or not secret_key:
            return Response({'error': 'Missing required S3 configuration fields or cloud_storage_id.'}, status=status.HTTP_400_BAD_REQUEST)
        dest_filename = f"cloud_restore_{_uuid.uuid4().hex[:8]}.tar.gz"
        backups_dir = os.path.join('/app', 'backups', 'server')
        os.makedirs(backups_dir, exist_ok=True)
        dest_path = os.path.join(backups_dir, dest_filename)

        if not download_from_s3(s3_bucket, s3_key, dest_path, endpoint=endpoint, region=region, access_key=access_key, secret_key=secret_key):
            return Response({'error': 'Failed to download backup from cloud storage. Check credentials and key.'}, status=status.HTTP_400_BAD_REQUEST)

        svc = BackupService()
        try:
            dest_path = svc._maybe_encrypt(dest_path)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.remove(dest_path)
            return Response({'error': f'Failed to encrypt downloaded backup: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        file_size = os.path.getsize(dest_path)
        backup = ServerBackup.objects.create(
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            error_message=f'Restored from cloud: {s3_bucket}/{s3_key}',
        )

        encryption_key = _resolve_encryption_key(request)
        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(
            backup_id=str(backup.id),
            encryption_key=encryption_key,
            requesting_user_id=request.user.id,
        )

        return Response({
            'status': 'Restore started from cloud backup.',
            'backup_id': str(backup.id),
            'file_size': file_size,
        })

