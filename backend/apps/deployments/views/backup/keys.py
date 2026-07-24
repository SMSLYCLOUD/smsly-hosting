"""key management mixin."""
import contextlib
import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models.audit import AuditLog

logger = logging.getLogger(__name__)




class KeyManagementMixin:
    """KeyManagement actions for the viewset."""


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
        from ...services.backup_service import (
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


    @action(detail=False, methods=['get'], url_path='list-keys')
    def list_keys(self, request):
        """Return stored backup encryption keys (fingerprints only, no key material)."""
        if not request.user.is_superuser:
            return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)
        from apps.deployments.models.backup import BackupEncryptionKey
        keys = BackupEncryptionKey.objects.all().order_by('-created_at')
        return Response([
            {
                'id': str(k.id),
                'key_id': k.key_id,
                'fingerprint': k.fingerprint,
                'label': k.label,
                'source': k.source,
                'is_active': k.is_active,
                'created_at': k.created_at.isoformat() if k.created_at else None,
            }
            for k in keys
        ])


    @action(detail=False, methods=['post'], url_path='delete-key')
    def delete_key(self, request):
        """Delete a stored backup encryption key by id. Admin only.
        Cannot delete the active (AUTO) key."""
        from apps.deployments.models.backup import BackupEncryptionKey
        if not request.user.is_superuser:
            return Response(
                {'error': 'Admin only.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        key_id_param = str(request.data.get('id') or '').strip()
        if not key_id_param:
            return Response(
                {'error': '"id" is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            key = BackupEncryptionKey.objects.get(id=key_id_param)
        except BackupEncryptionKey.DoesNotExist:
            return Response({'error': 'Key not found.'}, status=status.HTTP_404_NOT_FOUND)
        if key.is_active and key.source == 'AUTO':
            return Response(
                {'error': 'Cannot delete the active local encryption key.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        key.delete()
        return Response({'deleted': True, 'id': key_id_param})
