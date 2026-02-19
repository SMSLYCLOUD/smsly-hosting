from datetime import timedelta
import logging

from django.conf import settings
from django.utils import timezone

from .backup_service import BackupService

logger = logging.getLogger(__name__)


class ServerTransferService:
    def __init__(self, transfer):
        self.transfer = transfer

    def execute(self):
        """Run transfer pipeline with explicit stage transitions."""
        try:
            self.transfer.status = 'PREPARING'
            self.transfer.save(update_fields=['status'])
            self._prepare()

            self.transfer.status = 'UPLOADING'
            self.transfer.save(update_fields=['status'])
            self._upload()

            self.transfer.status = 'RESTORING'
            self.transfer.save(update_fields=['status'])
            self._restore()

            self.transfer.status = 'DNS_CUTOVER'
            self.transfer.save(update_fields=['status'])
            self._dns_cutover()

            self.transfer.status = 'VERIFYING'
            self.transfer.save(update_fields=['status'])
            self._verify()

            self._complete()
        except Exception as exc:
            self._handle_failure(exc)

    def _prepare(self):
        """Step 1: create source backup."""
        self._update(10, 'Creating backup on source server...')
        if self.transfer.transfer_type != 'SERVICE' or not self.transfer.service:
            raise NotImplementedError('FULL server transfer preparation is not implemented.')

        backup_svc = BackupService()
        backup = backup_svc.backup_service(self.transfer.service.id)
        self.transfer.source_backup = backup
        self.transfer.save(update_fields=['source_backup'])

    def _upload(self):
        """Step 2: upload backup to target."""
        self._update(40, 'Transferring backup to target server...')
        self._require_real_transfer_pipeline('upload')

    def _restore(self):
        """Step 3: restore on target."""
        self._update(70, 'Restoring services on target server...')
        self._require_real_transfer_pipeline('restore')

    def _dns_cutover(self):
        """Step 4: update DNS to target."""
        self._update(85, 'DNS cutover: update A records to new server IP...')
        self._require_real_transfer_pipeline('dns_cutover')

    def _verify(self):
        """Step 5: verify target health."""
        self._update(95, 'Verifying services on target server...')
        self._require_real_transfer_pipeline('verify')

    def _complete(self):
        """Step 6: mark successful and set rollback window."""
        self.transfer.status = 'COMPLETED'
        self.transfer.completed_at = timezone.now()
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=48)
        # Never keep private keys after transfer completion.
        self.transfer.target_ssh_key = ''
        self.transfer.save(
            update_fields=['status', 'completed_at', 'rollback_deadline', 'target_ssh_key']
        )
        self._update(100, 'Transfer complete! Source preserved for 48h rollback.')

    def rollback(self):
        """Revert transfer if rollback window is still open."""
        if not self.transfer.can_rollback:
            raise ValueError('Rollback not allowed')

        self.transfer.status = 'ROLLED_BACK'
        self.transfer.can_rollback = False
        self.transfer.target_ssh_key = ''
        self.transfer.save(update_fields=['status', 'can_rollback', 'target_ssh_key'])

    def _update(self, percent, step):
        self.transfer.progress_percent = percent
        self.transfer.current_step = step
        self.transfer.save(update_fields=['progress_percent', 'current_step'])

    def _require_real_transfer_pipeline(self, stage):
        if getattr(settings, 'ALLOW_STUB_TRANSFER_PIPELINE', False):
            logger.warning(
                'Transfer %s running %s stage in stub mode.',
                self.transfer.id,
                stage,
            )
            return
        raise NotImplementedError(f"Server transfer stage '{stage}' is not implemented.")

    def _handle_failure(self, error):
        logger.error('Transfer %s failed: %s', self.transfer.id, error)
        self.transfer.status = 'FAILED'
        self.transfer.error_message = str(error)
        self.transfer.target_ssh_key = ''
        self.transfer.save(update_fields=['status', 'error_message', 'target_ssh_key'])
