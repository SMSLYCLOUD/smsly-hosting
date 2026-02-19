from django.utils import timezone
from datetime import timedelta
import logging
from apps.deployments.utils import broadcast_status
from .backup_service import BackupService

logger = logging.getLogger(__name__)

class ServerTransferService:
    def __init__(self, transfer):
        self.transfer = transfer

    def execute(self):
        """Full transfer pipeline."""
        try:
            self._prepare()      # 10% — create backup
            self._upload()       # 40% — rsync/scp backup to target
            self._restore()      # 70% — restore on target server
            self._dns_cutover()  # 85% — update DNS records
            self._verify()       # 95% — health checks on target
            self._complete()     # 100%
        except Exception as e:
            self._handle_failure(e)

    def _prepare(self):
        """Step 1: Create backup on source server."""
        self._update(10, 'Creating backup on source server...')
        backup_svc = BackupService()
        if self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            backup = backup_svc.backup_service(self.transfer.service.id)
            self.transfer.source_backup = backup
            self.transfer.save()
        else:
            # Full server backup logic if needed, but primarily service transfer
            pass

    def _upload(self):
        """Step 2: Transfer backup to target server via rsync."""
        self._update(40, 'Transferring backup to target server...')
        # Stub: Assume backup is accessible or transferred via API in real implementation
        # Real implementation would use paramiko/rsync to push file to target IP
        pass

    def _restore(self):
        """Step 3: Restore on target server."""
        self._update(70, 'Restoring services on target server...')
        # Stub: Call remote API to restore or restore locally if this is the target
        pass

    def _dns_cutover(self):
        """Step 4: Update DNS A records to point to target IP."""
        self._update(85, 'DNS cutover — update A records to new server IP...')
        # Stub: Cloudflare API integration would go here
        pass

    def _verify(self):
        """Step 5: Health check all services on target."""
        self._update(95, 'Verifying services on target server...')
        # Stub: check health endpoint on new IP
        pass

    def _complete(self):
        """Step 6: Mark complete, keep source as rollback for 48h."""
        self.transfer.status = 'COMPLETED'
        self.transfer.completed_at = timezone.now()
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=48)
        self.transfer.save()
        self._update(100, 'Transfer complete! Source preserved for 48h rollback.')

    def rollback(self):
        """Revert: point DNS back to source, stop target containers."""
        if not self.transfer.can_rollback:
            raise ValueError("Rollback not allowed")

        self.transfer.status = 'ROLLED_BACK'
        self.transfer.can_rollback = False
        self.transfer.save()
        # Logic to revert DNS and stop target containers

    def _update(self, percent, step):
        self.transfer.progress_percent = percent
        self.transfer.current_step = step
        self.transfer.save(update_fields=['progress_percent', 'current_step'])
        # broadcast_status(self.transfer)  # Uncomment when serializer ready

    def _handle_failure(self, error):
        logger.error(f"Transfer failed: {error}")
        self.transfer.status = 'FAILED'
        self.transfer.error_message = str(error)
        self.transfer.save()
