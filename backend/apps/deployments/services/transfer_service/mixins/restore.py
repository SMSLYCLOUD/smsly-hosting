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
        remote_backup_path = (
            self._uploaded_remote_backup_path
            or f"/tmp/{backup_filename}"
        )

        if self.transfer.transfer_type == 'SERVICE':
            self._restore_single_service(remote_backup_path)
        else:
            self._restore_full_server_rest(remote_backup_path)
