import contextlib
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase

from apps.deployments.models.backup import BackupEncryptionKey
from apps.deployments.services.backup_service import BackupService
from apps.deployments.services.transfer_service import ServerTransferService


class ExportBackupKeyShapeTests(TestCase):
    """_export_backup_key writes a JSON bundle with the correct
    shape and only for FULL transfers.
    """

    def _make_service(self, transfer_type='FULL', source_ip='10.0.0.1'):
        transfer = SimpleNamespace(
            id='test-transfer-1',
            transfer_type=transfer_type,
            target_server_ip='10.0.0.2',
            source_server_ip=source_ip,
            source_backup=None,
            source_server_backup=None,
            source_server=None,
            logs='',
        )
        svc = ServerTransferService(transfer)
        svc.transfer = transfer
        log_calls = []
        svc._log = log_calls.append
        return svc, log_calls

    def test_writes_json_bundle_with_required_fields(self):
        svc, _ = self._make_service(transfer_type='FULL')
        key_material = Fernet.generate_key().decode()
        fingerprint = BackupService.compute_backup_key_fingerprint(key_material)
        BackupEncryptionKey.objects.create(
            key_id='a1b2c3d4',
            fingerprint=fingerprint,
            key_material_encrypted=key_material,
            source='AUTO',
            is_active=True,
        )
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key_material}, clear=False):
            path = svc._export_backup_key()
        self.assertIsNotNone(path)
        try:
            with open(path) as f:
                bundle = json.load(f)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)
        self.assertEqual(bundle['key_id'], 'a1b2c3d4')
        self.assertEqual(bundle['key_material'], key_material)
        self.assertEqual(bundle['fingerprint'], fingerprint)
        self.assertTrue(bundle['source_label'].startswith('migrated-from-'))
        self.assertIn('10.0.0.1', bundle['source_label'])

    def test_service_transfer_returns_none(self):
        """SERVICE transfers must NOT trigger a key export bundle."""
        svc, _ = self._make_service(transfer_type='SERVICE')
        key_material = Fernet.generate_key().decode()
        fingerprint = BackupService.compute_backup_key_fingerprint(key_material)
        BackupEncryptionKey.objects.create(
            key_id='b1b2c3d4',
            fingerprint=fingerprint,
            key_material_encrypted=key_material,
            is_active=True,
        )
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key_material}, clear=False):
            path = svc._export_backup_key()
        self.assertIsNone(path)

    def test_no_env_key_returns_none(self):
        svc, _ = self._make_service(transfer_type='FULL')
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': ''}, clear=False):
            path = svc._export_backup_key()
        self.assertIsNone(path)

    def test_no_matching_active_row_returns_none_and_logs(self):
        """When BACKUP_ENCRYPTION_KEY is set on the source but no
        BackupEncryptionKey row matches the active fingerprint,
        the export is skipped and a clear log line is written.
        """
        svc, logs = self._make_service(transfer_type='FULL')
        key_material = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key_material}, clear=False):
            path = svc._export_backup_key()
        self.assertIsNone(path)
        joined = '\n'.join(logs)
        self.assertIn('No active BackupEncryptionKey', joined)

    def test_inactive_row_with_matching_fingerprint_not_used(self):
        """Only is_active=True rows are exported. An inactive row
        with the same fingerprint must not be used.
        """
        svc, _ = self._make_service(transfer_type='FULL')
        key_material = Fernet.generate_key().decode()
        fingerprint = BackupService.compute_backup_key_fingerprint(key_material)
        BackupEncryptionKey.objects.create(
            key_id='c1b2c3d4',
            fingerprint=fingerprint,
            key_material_encrypted=key_material,
            source='IMPORTED',
            is_active=False,
        )
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key_material}, clear=False):
            path = svc._export_backup_key()
        self.assertIsNone(path)


class ExportBackupKeyMockedTests(TestCase):
    """Hermetic version: mocks BackupService to avoid coupling
    the bundle shape test to fingerprint helpers.
    """

    def test_bundle_uses_compute_fingerprint_and_looks_up_active_row(self):
        svc = ServerTransferService(SimpleNamespace(
            id='t', transfer_type='FULL', target_server_ip='10.0.0.2',
            source_server_ip='10.0.0.1', source_backup=None,
            source_server_backup=None, source_server=None, logs='',
        ))
        svc._log = lambda *a, **kw: None
        key_material = Fernet.generate_key().decode()

        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key_material}, clear=False):
            with patch.object(
                BackupService, 'compute_backup_key_fingerprint',
                return_value='deadbeef',
            ) as mock_fp, patch.object(
                BackupService, 'import_backup_key',
            ) as mock_import:
                mock_row = SimpleNamespace(key_id='aabbccdd', fingerprint='deadbeef')
                with patch(
                    'apps.deployments.models_backup.BackupEncryptionKey.objects',
                ) as mock_mgr:
                    mock_mgr.filter.return_value.first.return_value = mock_row
                    path = svc._export_backup_key()
        self.assertIsNotNone(path)
        try:
            with open(path) as f:
                bundle = json.load(f)
        finally:
            os.unlink(path)
        mock_fp.assert_called_once_with(key_material)
        self.assertEqual(bundle['key_id'], 'aabbccdd')
        self.assertEqual(bundle['fingerprint'], 'deadbeef')
        self.assertEqual(bundle['key_material'], key_material)
        self.assertIn('migrated-from-10.0.0.1', bundle['source_label'])
        mock_import.assert_not_called()
