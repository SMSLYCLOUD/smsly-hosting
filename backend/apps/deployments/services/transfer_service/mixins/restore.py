"""RestoreMixin — restore-related transfer methods."""

import logging

from ..helpers import _safe_backup_basename

from .env_remap import EnvRemapMixin
from .server_restore import ServerRestoreMixin
from .service_restore import SingleServiceRestoreMixin
from .target_setup import TargetSetupMixin

logger = logging.getLogger(__name__)


class RestoreMixin(
    SingleServiceRestoreMixin,
    ServerRestoreMixin,
    EnvRemapMixin,
    TargetSetupMixin,
):
    def _restore(self):
        self._update(60, 'Restoring services on target server...')

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        backup_filename = _safe_backup_basename(backup.file_path)
        # Guard: _upload() may not have run (checkpoint resume, task retry).
        # Fall back to the standard /tmp path the upload would have used,
        # and log if we're guessing so the operator sees it in the log.
        remote_backup_path = (
            getattr(self, '_uploaded_remote_backup_path', None)
            or f"/tmp/{backup_filename}"
        )
        if not getattr(self, '_uploaded_remote_backup_path', None):
            logger.warning(
                "Transfer %s: _uploaded_remote_backup_path not set (upload "
                "phase skipped or retry) — using default /tmp/%s. If the "
                "target lacks this file, the restore will fail with a clear "
                "tar/untar error.",
                self.transfer.id, backup_filename,
            )

        if self.transfer.transfer_type == 'SERVICE':
            self._restore_single_service(remote_backup_path)
        else:
            self._restore_full_server_rest(remote_backup_path)
