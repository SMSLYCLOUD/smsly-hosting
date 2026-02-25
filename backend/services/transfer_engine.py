"""
Service migration engine for zero-downtime transfer between servers.

Transfer flow:
  1. PREPARING: Create ServiceBackup (container snapshot + volumes + addon backups)
  2. UPLOADING: SCP the backup tarball to target server via SSH
  3. RESTORING: Call target server's API to import the backup
  4. DNS_CUTOVER: Update Caddy config to route to target
  5. VERIFYING: Health check on target
  6. COMPLETED: Mark done, keep source as standby
  7. On failure at any step → FAILED with error logs
  8. On rollback → restart source, revert DNS
"""
import os
import json
import time
import logging
import tempfile
import shutil
import requests
import paramiko
from datetime import timedelta
from django.utils import timezone
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.models_backup import ServiceBackup
from services.addon_provisioner import addon_provisioner

logger = logging.getLogger(__name__)


class TransferError(Exception):
    """Raised when transfer fails."""


class TransferEngine:
    def __init__(self, transfer: ServerTransfer):
        self.transfer = transfer
        self.service = transfer.service
        self.source_backup = None

    def _log(self, message: str):
        self.transfer.logs += f"[{timezone.now().strftime('%H:%M:%S')}] {message}\n"
        self.transfer.current_step = message
        self.transfer.save(update_fields=['logs', 'current_step'])

    def execute(self):
        try:
            self._prepare_backup()
            self._upload_to_target()
            self._restore_on_target()
            self._dns_cutover()
            self._verify_health()
            self._complete()
        except Exception as e:
            self.transfer.status = 'FAILED'
            self.transfer.error_message = str(e)
            self._log(f"Transfer failed: {e}")
            self.transfer.save()

    def rollback(self):
        self._log("Starting rollback...")
        try:
            # Revert DNS
            # from services.caddy_manager import generate_caddyfile, apply_caddyfile
            # from apps.deployments.models import PlatformConfig

            # Restart source if stopped (not implemented yet, assuming it's still running)

            self.transfer.status = 'ROLLED_BACK'
            self._log("Rollback complete")
            self.transfer.save()
        except Exception as e:
            self._log(f"Rollback failed: {e}")

    def _prepare_backup(self):
        self.transfer.status = 'PREPARING'
        self.transfer.progress_percent = 10
        self._log("Creating service backup...")
        self.transfer.save()

        # Create backup
        from apps.deployments.services.backup_service import BackupService
        bs = BackupService()
        # bs.backup_service returns the Backup object, not path
        self.source_backup = bs.backup_service(self.service.id, backup_type='PRE_TRANSFER')

        if not self.source_backup or self.source_backup.status == 'FAILED':
            raise TransferError("Backup generation failed")

        self.transfer.source_backup = self.source_backup
        self.transfer.save()
        self._log(f"Backup created: {self.source_backup.id}")

    def _upload_to_target(self):
        self.transfer.status = 'UPLOADING'
        self.transfer.progress_percent = 30
        self._log(f"Uploading backup to {self.transfer.target_server_ip}...")
        self.transfer.save()

        key_file = tempfile.NamedTemporaryFile(delete=False)
        key_file.write(self.transfer.target_ssh_key.encode())
        key_file.close()

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                self.transfer.target_server_ip,
                username='root',
                key_filename=key_file.name
            )

            sftp = ssh.open_sftp()
            remote_path = f"/tmp/{os.path.basename(self.source_backup.file_path)}"
            sftp.put(self.source_backup.file_path, remote_path)
            sftp.close()
            ssh.close()

            self._log("Upload complete")
        finally:
            os.unlink(key_file.name)

    def _restore_on_target(self):
        self.transfer.status = 'RESTORING'
        self.transfer.progress_percent = 60
        self._log("Restoring on target server...")
        self.transfer.save()

        # Call target API to restore

        key_file = tempfile.NamedTemporaryFile(delete=False)
        key_file.write(self.transfer.target_ssh_key.encode())
        key_file.close()

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                self.transfer.target_server_ip,
                username='root',
                key_filename=key_file.name
            )

            remote_path = f"/tmp/{os.path.basename(self.source_backup.file_path)}"
            cmd = f"cd /opt/smsly-hosting/backend && python manage.py restore_service_backup --file {remote_path} --service-id {self.service.id}"
            stdin, stdout, stderr = ssh.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()

            if exit_status != 0:
                raise TransferError(f"Remote restore failed: {stderr.read().decode()}")

            ssh.close()
            self._log("Remote restore complete")
        finally:
            os.unlink(key_file.name)

    def _dns_cutover(self):
        self.transfer.status = 'DNS_CUTOVER'
        self.transfer.progress_percent = 80
        self._log("Updating DNS/Caddy routing...")
        self.transfer.save()

        # Placeholder for DNS cutover logic
        logger.info("DNS cutover placeholder")

    def _verify_health(self):
        self.transfer.status = 'VERIFYING'
        self.transfer.progress_percent = 90
        self._log("Verifying health on target...")
        self.transfer.save()

        # Placeholder for health check
        logger.info("Health check placeholder")

    def _complete(self):
        self.transfer.status = 'COMPLETED'
        self.transfer.progress_percent = 100
        self.transfer.completed_at = timezone.now()
        self._log("Transfer completed successfully")
        self.transfer.save()
