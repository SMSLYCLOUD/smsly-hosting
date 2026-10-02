import contextlib
import json
import logging
import os
import tempfile

from ...backup_service import BackupService, UnknownBackupKeyIdError
from ..helpers import _safe_backup_basename

logger = logging.getLogger(__name__)


class UploadMixin:
    # Raw bytes per upload chunk. Base64 inflates ~33%, keeping each
    # JSON body around ~1.8MB — safely below gunicorn/proxy limits
    # while keeping chunk counts sane for multi-hundred-MB backups.
    UPLOAD_CHUNK_BYTES = 1024 * 1024

    def _upload(self):
        self._update(40, 'Preparing backup for restore...')

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        if not backup or not backup.file_path:
            raise ValueError("Backup file not found.")

        local_path = backup.file_path
        if local_path.endswith(".enc"):
            key = BackupService._get_encryption_key()
            if not key:
                raise ValueError("Encrypted backup detected but no backup encryption key is configured.")
            try:
                local_path = BackupService.decrypt_backup(local_path, key)
            except UnknownBackupKeyIdError as exc:
                raise ValueError(
                    f"Backup encrypted with unknown key_id={exc.key_id} "
                    f"(fingerprint={exc.fingerprint}). "
                    "Call POST /api/v1/backups/import-key/ on the target with the "
                    "source's key_id and BACKUP_ENCRYPTION_KEY to register the "
                    "foreign key, then retry the transfer."
                ) from exc

        remote_path = f"/tmp/{_safe_backup_basename(local_path)}"
        self._uploaded_remote_backup_path = local_path if self._target_is_local() else remote_path

        if self._target_is_local():
            self._log(f"Backup prepared at {local_path} (local transfer — no upload needed)")
            return

        # Ship the archive to the target over the node API (chunked —
        # the restore step only reads the file, nothing else delivers
        # it; 2026-10-02: every transfer died with No such file on /tmp).
        self._upload_backup_to_target(local_path, remote_path)

    def _upload_backup_to_target(self, local_path: str, remote_path: str) -> int:
        """POST a local file to the target's incoming/upload-file in chunks.

        Returns total bytes written. Raises on any chunk failure —
        fail loud here rather than with a confusing missing-file error
        in the restore step.
        """
        import base64

        size = os.path.getsize(local_path)
        self._log(f"Uploading backup to target ({size} bytes, chunked)...")
        offset = 0
        with open(local_path, 'rb') as handle:
            while True:
                data = handle.read(self.UPLOAD_CHUNK_BYTES)
                if not data:
                    break
                body = {
                    'path': remote_path,
                    'content_base64': base64.b64encode(data).decode('ascii'),
                    'offset': offset,
                    'append': offset > 0,
                }
                resp = self._node_api_request('incoming/upload-file', body=body, timeout=180)
                if not isinstance(resp, dict) or resp.get('status') != 'written':
                    raise RuntimeError(f"Target upload failed at offset {offset}: {resp}")
                offset += len(data)
                if offset % (10 * self.UPLOAD_CHUNK_BYTES) == 0:
                    self._log(f"Upload progress: {offset}/{size} bytes...")
        if offset != size:
            raise RuntimeError(f"Upload incomplete: wrote {offset} of {size} bytes")
        self._log(f"Upload complete: {offset} bytes to {remote_path}")
        return offset

    def _export_backup_key(self) -> str | None:
        if self.transfer.transfer_type != 'FULL':
            return None
        key_material = BackupService._get_encryption_key()
        if not key_material:
            return None
        try:
            fingerprint = BackupService.compute_backup_key_fingerprint(key_material)
        except Exception as exc:
            self._log(f"Could not compute backup key fingerprint: {exc}")
            return None
        try:
            from ....models.backup import BackupEncryptionKey
            row = (
                BackupEncryptionKey.objects
                .filter(is_active=True, fingerprint=fingerprint)
                .first()
            )
        except Exception as exc:
            self._log(f"Could not look up active BackupEncryptionKey: {exc}")
            row = None
        if row is None:
            self._log(
                "No active BackupEncryptionKey row found for source's "
                f"BACKUP_ENCRYPTION_KEY (fingerprint={fingerprint}). "
                "The target will generate a new key on first use; historical "
                "backups created on the source will require manual key import."
            )
            return None
        source_ip = (
            self.transfer.source_server_ip
            or getattr(self.transfer.source_server, "host", None)
            or "unknown"
        )
        bundle = {
            'key_id': row.key_id,
            'key_material': key_material,
            'fingerprint': fingerprint,
            'source_label': f'migrated-from-{source_ip}',
        }
        fd, path = tempfile.mkstemp(prefix='backup_key_export_', suffix='.json')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(bundle, f)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(path)
            raise
        return path
