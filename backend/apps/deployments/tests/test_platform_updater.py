from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.deployments.services import platform_updater
from apps.deployments.services.platform_updater import perform_update

from apps.deployments.models.updates import PlatformUpdate

User = get_user_model()


class PlatformUpdaterTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="admin", password="pwd")
        self.update = PlatformUpdate.objects.create(status='PENDING')
        self.tmpdir = TemporaryDirectory()
        self.watch_dir = Path(self.tmpdir.name)
        self.patches = [
            patch.object(platform_updater, 'UPDATE_WATCH_DIR', self.watch_dir),
            patch.object(platform_updater, 'UPDATE_FLAG', self.watch_dir / '.update'),
            patch.object(platform_updater, 'UPDATE_STATUS', self.watch_dir / '.update.status'),
            patch('apps.core.services.audit_service.AuditService.log'),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmpdir.cleanup)

    @patch('apps.deployments.services.platform_updater._run')
    def test_prevent_concurrent_updates(self, mock_run):
        PlatformUpdate.objects.create(status='MIGRATING')

        result = perform_update(self.update)

        self.update.refresh_from_db()
        self.assertFalse(result)
        self.assertEqual(self.update.status, 'FAILED')
        self.assertIn('Another update is currently in progress', self.update.error_message)
        mock_run.assert_not_called()

    @patch('apps.deployments.services.platform_updater.check_health', return_value=True)
    @patch('apps.deployments.services.platform_updater._wait_for_watcher', return_value=True)
    @patch('apps.deployments.services.platform_updater._run', return_value=(True, 'abc123\n'))
    def test_update_writes_watcher_flag_and_completes(self, _mock_run, mock_wait, _mock_health):
        result = perform_update(self.update)

        self.update.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(self.update.status, 'COMPLETED')
        self.assertEqual(self.update.progress_percent, 100)
        self.assertEqual((self.watch_dir / '.update').read_text(), f'update:{self.update.id}\n')
        mock_wait.assert_called_once_with(self.update)

    @patch('apps.deployments.services.platform_updater.check_health', return_value=False)
    @patch('apps.deployments.services.platform_updater._wait_for_watcher', return_value=True)
    @patch('apps.deployments.services.platform_updater._run', return_value=(True, 'abc123\n'))
    def test_update_fails_if_service_unhealthy(self, _mock_run, _mock_wait, _mock_health):
        result = perform_update(self.update)

        self.assertFalse(result)
        self.update.refresh_from_db()
        self.assertEqual(self.update.status, 'FAILED')
        self.assertIn('Health check failed after update', self.update.error_message)

    def test_wait_for_watcher_reports_failure_status(self):
        platform_updater.UPDATE_STATUS.write_text(
            f'STATE=failed\nREQUEST_ID={self.update.id}\nMODE=update\nEXIT_CODE=1\nMESSAGE=installer failed\n',
            encoding='utf-8',
        )

        result = platform_updater._wait_for_watcher(self.update)

        self.assertFalse(result)
        self.update.refresh_from_db()
        self.assertEqual(self.update.error_message, 'installer failed')
