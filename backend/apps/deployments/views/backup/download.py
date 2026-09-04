"""download mixin."""
import logging
import os

from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.auth import CookieAwareTokenAuthentication
from rest_framework.authentication import TokenAuthentication

from .._helpers import (
    _generate_signed_download_url,
    _open_backup_download_response,
    _verify_signed_download,
)

logger = logging.getLogger(__name__)


from ...services.backup_service import BackupService, UnknownBackupKeyIdError


class DownloadActionsMixin:
    """DownloadActions actions for the viewset."""


    @action(detail=True, methods=['get'], url_path='header', permission_classes=[permissions.IsAuthenticated], authentication_classes=[CookieAwareTokenAuthentication, TokenAuthentication])
    def header(self, request, pk=None):
        """Return the V2 backup header (key_id, fingerprint) so the
        operator can copy the key_id to a different master for the
        ``import-key`` flow. Returns 404 if the backup is not in V2
        format.

        Requires authentication (owner of the backup's service or
        superuser). An earlier draft documented this as intentionally
        public, but key_id + fingerprint is reconnaissance material
        for an attacker targeting encrypted backups — it must not be
        anonymously enumerable.
        """
        backup = self.get_object()
        if not self._user_can_access_service(request.user, getattr(backup, 'service', None)):
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(info)


    @action(detail=True, methods=['get'], url_path='download-key', permission_classes=[permissions.IsAuthenticated], authentication_classes=[CookieAwareTokenAuthentication, TokenAuthentication])
    def download_key(self, request, pk=None):
        """Download the V2 backup header as a .key.json file alongside
        the backup. The operator stores this file with the backup and
        uses ``POST /api/v1/backups/import-key/`` on the target master
        to import the key before restoring.

        The file is safe to distribute alongside the backup — it
        contains only the public key_id and fingerprint, NOT the
        encryption key material itself. The key material must be
        transferred via a separate secure channel (the
        ``BackupEncryptionKey`` table on the source master, or an
        out-of-band exchange).
        """
        backup = self.get_object()
        # Ownership gate — without it any authenticated user could read
        # the backup's key_id/fingerprint by UUID probing.
        if not self._user_can_access_service(request.user, getattr(backup, 'service', None)):
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        key_payload = {
            'backup_id': str(backup.id),
            'service_name': getattr(getattr(backup, 'service', None), 'name', None),
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

        import json as _json

        from django.http import HttpResponse
        response = HttpResponse(
            _json.dumps(key_payload, indent=2),
            content_type='application/json',
        )
        filename = f"backup-{backup.id}-key.json"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


    @action(detail=True, methods=['get'], permission_classes=[permissions.IsAuthenticated], authentication_classes=[CookieAwareTokenAuthentication, TokenAuthentication])
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
        elif not request.user.is_superuser:
            # Server backups contain the full platform state — only
            # superusers may download them without a signed URL.
            return Response(
                {'error': 'Not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Bypass get_queryset() which filters by request.user — signed/AllowAny
        # requests have an AnonymousUser that crashes the queryset filter.
        backup = self.queryset.model.objects.filter(pk=pk).first()
        if not backup:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

        # Ownership gate: authenticated downloaders must own the backup's service.
        # Signed URLs bypass this (already verified above). Without this, an
        # authenticated user could brute-force UUIDs and download any backup.
        if not signed_value and request.user.is_authenticated:
            if not self._user_can_access_service(request.user, backup.service):
                return Response(
                    {'error': 'Not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        file_path = backup.file_path

        if not file_path or not os.path.exists(file_path):
            # File missing locally — try to download from cloud storage
            from ...services.backup_service import _download_backup_from_cloud
            if getattr(backup, 'cloud_uploaded', False):
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                if _download_backup_from_cloud(backup, file_path):
                    logger.info("Downloaded backup %s from cloud to %s", backup.id, file_path)
                else:
                    return Response({'error': 'Backup file not found locally and cloud download failed.'}, status=status.HTTP_404_NOT_FOUND)
            else:
                return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)

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
                error_msg = str(e).lower()
                if 'hmac' in error_msg or 'invalid' in error_msg or 'tag' in error_msg:
                    try:
                        current_fp = BackupService.compute_backup_key_fingerprint(key)
                    except Exception:
                        current_fp = 'unknown'
                    return Response(
                        {
                            'error': 'Encryption key mismatch.',
                            'detail': (
                                'The backup was encrypted with a different key than the '
                                'current BACKUP_ENCRYPTION_KEY. The backup cannot be '
                                'decrypted with the current key.'
                            ),
                            'current_fingerprint': current_fp,
                            'remediation': (
                                'Either restore the original encryption key that was used '
                                'when this backup was created, or delete this backup and '
                                'create a new one with the current key.'
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                return Response(
                    {'error': f'Failed to decrypt backup: {e}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        return _open_backup_download_response(
            request,
            file_path,
            os.path.basename(file_path),
        )


    @action(detail=True, methods=['get'], url_path='download-url')
    def download_url(self, request, pk=None):
        backup = self.get_object()
        return Response({'url': _generate_signed_download_url(request, str(backup.id), 'backup-download', path_params={'pk': str(backup.id)})})

    # ── Verify integrity ────────────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='verify')
    def verify(self, request, pk=None):
        """POST /api/v1/backups/{id}/verify/

        Runs integrity verification (checksum + archive validity) on
        this backup synchronously and returns the result immediately.
        """
        import hashlib as _hashlib
        import os as _os
        import tarfile as _tarfile

        backup = self.get_object()
        filepath = backup.file_path
        errors = []
        passed = False
        _decrypted_tmp = None

        try:
            if not filepath or not _os.path.exists(filepath):
                raise FileNotFoundError(f"Backup file not found: {filepath}")

            expected_hash = (getattr(backup, 'metadata', None) or {}).get('checksum_sha256', '')
            if expected_hash:
                sha = _hashlib.sha256()
                with open(filepath, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b''):
                        sha.update(chunk)
                if sha.hexdigest() != expected_hash:
                    raise ValueError("Checksum mismatch — backup may be corrupted")

            # Encrypted backups are AES-GCM chunks, not gzip — decrypt to
            # a temp file before the tar test-open (same as the integrity
            # beat task).
            archive_to_check = filepath
            if filepath.endswith('.enc'):
                key = BackupService._get_encryption_key()
                if not key:
                    raise ValueError("Backup is encrypted but BACKUP_ENCRYPTION_KEY is unavailable")
                _decrypted_tmp = BackupService.decrypt_backup(filepath, key)
                archive_to_check = _decrypted_tmp

            with _tarfile.open(archive_to_check, 'r:gz') as tar:
                members = tar.getmembers()
                if not members:
                    raise ValueError("Archive is empty")

            passed = True
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if _decrypted_tmp:
                try:
                    os.remove(_decrypted_tmp)
                except OSError:
                    pass

        return Response({
            'status': 'completed',
            'backup_id': str(backup.id),
            'passed': passed,
            'errors': errors,
        })

    # ── Restore from local file upload ──────────────────────────────
