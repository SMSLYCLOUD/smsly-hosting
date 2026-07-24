"""restore mixin."""
import contextlib
import logging
import os

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from .._helpers import _resolve_encryption_key

logger = logging.getLogger(__name__)


from ...models import Service
from ...models.backup import ServiceBackup
from ...services.backup_service import BackupService, download_from_s3, normalize_s3_key
from ...services.server_guard import ServerGuard
from ...tasks import restore_service_backup_task


class RestoreActionsMixin:
    """RestoreActions actions for the viewset."""


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

        # ── Pre-flight: verify the encryption key is available ───────
        # If the backup is encrypted and we don't have the key (either
        # the local BACKUP_ENCRYPTION_KEY env var, a matching imported
        # key, or a key supplied in this request), refuse to queue
        # the task. Without this, the task would fail silently inside
        # the Celery worker (UnknownBackupKeyIdError), and the user
        # would see 'restore_started' followed by no progress.
        key_provided = _resolve_encryption_key(request)
        if backup.file_path and backup.file_path.endswith('.enc'):
            from ...services.backup_service import (
                BackupService,
            )
            # Check metadata stamp first (works for cloud-stored backups
            # where the local file doesn't exist yet), then fall back
            # to reading the V2 header from the file on disk.
            enc_meta = (backup.metadata or {}).get('encryption', {})
            meta_key_id = enc_meta.get('key_id', '')
            meta_fingerprint = enc_meta.get('fingerprint', '')
            # If metadata carries the key identity and we have a stored
            # key matching that fingerprint (either the active local key
            # or an imported key), the pre-flight passes without needing
            # the file on disk.
            meta_matched = False
            if meta_fingerprint:
                from ...services.backup_service import BackupService as BSC
                if key_provided:
                    try:
                        if BSC.compute_backup_key_fingerprint(key_provided) == meta_fingerprint:
                            meta_matched = True
                    except Exception:
                        pass
                if not meta_matched and BSC.lookup_key_by_id(meta_fingerprint):
                    meta_matched = True
                if not meta_matched and BSC.lookup_key_by_id(meta_key_id):
                    meta_matched = True
            if not meta_matched and not BackupService.can_decrypt_backup(
                backup.file_path, passed_key=key_provided,
            ):
                header_key_id = meta_key_id or 'unknown'
                # Try reading the header from the file for a better key_id
                try:
                    header = BackupService.read_v2_header(backup.file_path)
                    header_key_id = header.get('key_id', header_key_id)
                except (OSError, ValueError):
                    pass
                return Response(
                    {
                        'error': (
                            'Encryption key required. This backup '
                            'was encrypted on a different '
                            'master. Import the key or '
                            'provide it in the request.'
                        ),
                        'error_code': 'ENCRYPTION_KEY_REQUIRED',
                        'key_id': header_key_id,
                        'remediation': (
                            'POST /api/v1/backups/import-key/ with '
                            'key_id and key_material from '
                            'the source master, or send '
                            '"encryption_key" in the '
                            'restore request body, or '
                            'upload a key_file JSON.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        # ── Pre-flight safety snapshot check ─────────────
        # Attempt a synchronous PRE_TRANSFER snapshot. If it fails, warn
        # the user and ask them to confirm with "force": true. Without a
        # safety snapshot, a corrupt restore loses the active state
        # permanently.
        force = str(request.data.get('force', '')).lower() == 'true'
        if not force:
            try:
                from ...services.backup_service import BackupService
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
                            'Pre-restore safety snapshot could not be '
                            'created. Proceeding without a snapshot will '
                            'permanently destroy the current running state '
                            'if the restore archive is corrupt.'
                        ),
                        'snapshot_error': str(snap_exc),
                        'backup_id': str(backup.id),
                        'remediation': (
                            'Fix the snapshot error and retry, or send '
                            '"force": true to proceed without a safety '
                            'snapshot. Use force with caution — data '
                            'loss is possible if the backup is corrupt.'
                        ),
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

        restore_service_backup_task.delay(
            backup_id=str(backup.id),
            target_service_id=str(target_service_id) if target_service_id else None,
            requesting_user_id=request.user.id,
            raise_on_snapshot_failure=not force,
            encryption_key=key_provided or None,
        )
        return Response({'status': 'restore_started'})


    @action(detail=False, methods=['post'], url_path='upload-restore')
    def upload_restore(self, request):
        """POST /api/v1/backups/upload-restore/

        Upload a backup tar.gz file and restore it to a service.
        Body: multipart/form-data with ``file`` and ``service_id``.
        """
        file = request.FILES.get('file')
        service_id = request.data.get('service_id')
        if not file or not service_id:
            return Response({'error': 'file and service_id are required.'}, status=status.HTTP_400_BAD_REQUEST)

        MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
        if file.size > MAX_UPLOAD_SIZE:
            return Response(
                {'error': f'File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)}MB.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        try:
            target_service = Service.objects.get(id=service_id)
            if not self._user_can_access_service(request.user, target_service):
                return Response({'error': 'Service not found or access denied.'}, status=status.HTTP_404_NOT_FOUND)
        except Service.DoesNotExist:
            return Response({'error': 'Service not found.'}, status=status.HTTP_404_NOT_FOUND)

        import uuid as _uuid
        dest_filename = f"local_restore_{_uuid.uuid4().hex[:8]}.tar.gz"
        backups_dir = os.path.join('/app', 'backups', 'services', str(service_id))
        os.makedirs(backups_dir, exist_ok=True)
        dest_path = os.path.join(backups_dir, dest_filename)

        with open(dest_path, 'wb+') as f:
            for chunk in file.chunks():
                f.write(chunk)

        file_size = os.path.getsize(dest_path)
        svc = BackupService()
        try:
            dest_path = svc._maybe_encrypt(dest_path)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.remove(dest_path)
            return Response({'error': f'Failed to process uploaded backup: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        backup = ServiceBackup.objects.create(
            service=target_service,
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            backup_type='MANUAL',
            error_message=f'Restored from local upload: {file.name}',
        )

        encryption_key = _resolve_encryption_key(request)
        from apps.deployments.tasks import restore_service_backup_task
        restore_service_backup_task.delay(
            backup_id=str(backup.id),
            target_service_id=str(service_id),
            requesting_user_id=request.user.id,
            encryption_key=encryption_key,
            raise_on_snapshot_failure=False,
        )

        return Response({
            'status': 'restore_started',
            'backup_id': str(backup.id),
            'file_name': file.name,
        })

    # ── Restoration history ─────────────────────────────────────────


    @action(detail=False, methods=['post'], url_path='restore-from-cloud')
    def restore_from_cloud(self, request):
        """Restore a service backup directly from cloud storage."""
        cloud_storage_id = request.data.get('cloud_storage_id')
        s3_key = request.data.get('s3_key', '').strip()
        service_id = request.data.get('service_id')

        if not service_id:
            return Response({'error': 'Missing required service_id.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target_service = Service.objects.get(id=service_id)
            if not self._user_can_access_service(request.user, target_service):
                return Response({'error': 'Target service not found or access denied.'}, status=status.HTTP_404_NOT_FOUND)
        except Service.DoesNotExist:
            return Response({'error': 'Target service not found.'}, status=status.HTTP_404_NOT_FOUND)

        if cloud_storage_id:
            from apps.deployments.models.cloud_storage import CloudStorageDestination
            try:
                dest = CloudStorageDestination.objects.get(id=cloud_storage_id)
                # Scope: only platform-wide or same-service destinations
                if dest.service is not None and str(dest.service.id) != service_id:
                    return Response(
                        {'error': 'This cloud destination belongs to a different service.'},
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
        import uuid as _uuid
        dest_filename = f"cloud_restore_{_uuid.uuid4().hex[:8]}.tar.gz"
        backups_dir = os.path.join('/app', 'backups', 'services', str(service_id))
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
        backup = ServiceBackup.objects.create(
            service=target_service,
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            backup_type='MANUAL',
            error_message=f'Restored from cloud: {s3_bucket}/{s3_key}',
        )

        # ── Pre-flight safety snapshot check ─────────────
        force = str(request.data.get('force', '')).lower() == 'true'
        if not force:
            try:
                BackupService().backup_service(
                    target_service.id, backup_type='PRE_TRANSFER',
                )
            except Exception as snap_exc:
                logger.warning(
                    "Pre-restore snapshot failed for service %s during cloud restore: %s",
                    target_service.id, snap_exc,
                )
                with contextlib.suppress(OSError):
                    os.remove(dest_path)
                backup.status = 'FAILED'
                backup.error_message = f"Pre-restore snapshot failed: {snap_exc}"
                backup.save(update_fields=['status', 'error_message'])
                return Response(
                    {
                        'error': (
                            'Pre-restore safety snapshot could not be '
                            'created. Proceeding without a snapshot will '
                            'permanently destroy the current running state '
                            'if the restore archive is corrupt.'
                        ),
                        'snapshot_error': str(snap_exc),
                        'backup_id': str(backup.id),
                        'remediation': (
                            'Fix the snapshot error and retry, or send '
                            '"force": true to proceed without a safety '
                            'snapshot. Use force with caution — data '
                            'loss is possible if the backup is corrupt.'
                        ),
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

        encryption_key = _resolve_encryption_key(request)
        from apps.deployments.tasks import restore_service_backup_task
        restore_service_backup_task.delay(
            backup_id=str(backup.id),
            target_service_id=str(service_id),
            requesting_user_id=request.user.id,
            encryption_key=encryption_key,
            raise_on_snapshot_failure=not force,
        )

        return Response({
            'status': 'Restore started from cloud backup.',
            'backup_id': str(backup.id),
            'file_size': file_size,
        })


    @action(detail=False, methods=['post'], url_path='list-backups')
    def list_cloud_backups(self, request):
        """List available backup files in a cloud storage bucket (service scope)."""
        cloud_storage_id = request.data.get('cloud_storage_id', '').strip()
        prefix = request.data.get('prefix', 'smsly-backups/').strip()
        service_id = request.data.get('service_id', '').strip()

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

        # Scope: only platform-wide destinations or same-service destinations
        if dest.service is not None and str(dest.service.id) != service_id:
            return Response(
                {'error': 'This cloud destination belongs to a different service.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from ...services.backup_service import list_s3_objects

        objects = list_s3_objects(
            bucket=dest.bucket,
            prefix=prefix,
            endpoint=dest.endpoint,
            region=dest.region,
            access_key=dest.access_key,
            secret_key=dest.secret_key,
        )

        return Response({'objects': objects, 'bucket': dest.bucket})
