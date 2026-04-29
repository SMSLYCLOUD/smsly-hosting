from django.test import TestCase
from unittest.mock import patch, MagicMock
from apps.deployments.models_updates import PlatformUpdate
from services.platform_updater import perform_update, PlatformUpdateError
from django.contrib.auth import get_user_model
import urllib.request

User = get_user_model()

class PlatformUpdaterTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="admin", password="pwd")
        self.update = PlatformUpdate.objects.create( status='PENDING')

    @patch('services.platform_updater._run')
    def test_prevent_concurrent_updates(self, mock_run):
        # Create an active update
        PlatformUpdate.objects.create( status='MIGRATING')

        result = perform_update(self.update)

        self.assertFalse(result)
        self.assertEqual(self.update.status, 'FAILED')
        self.assertIn('Another update is currently in progress', self.update.error_message)
        mock_run.assert_not_called()

    @patch('urllib.request.urlopen')
    @patch('services.platform_updater._run')
    def test_update_fails_if_service_unhealthy(self, mock_run, mock_urlopen):
        # Mock git commands and docker commands
        def mock_run_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            # Make the 'db' service fail healthcheck
            if "ps db" in cmd_str:
                return True, "starting"
            return True, "success"

        mock_run.side_effect = mock_run_side_effect
        mock_urlopen.return_value = MagicMock(status=200)

        # Trigger update
        result = perform_update(self.update)

        self.assertFalse(result)
        self.update.refresh_from_db()
        # Since it failed and _rollback is triggered, status becomes FAILED or ROLLED_BACK
        self.assertIn(self.update.status, ['FAILED', 'ROLLED_BACK'])
        self.assertIn('Service db failed to reach healthy state', self.update.error_message)
