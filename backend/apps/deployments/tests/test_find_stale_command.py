import io
from django.test import TestCase
from django.core.management import call_command
from apps.deployments.models_core import Service
from apps.deployments.models_addons import Addon
from unittest.mock import patch, MagicMock

class TestFindStaleCommand(TestCase):
    @patch('apps.deployments.management.commands.find_stale_runtime_resources.docker.from_env')
    def test_find_stale_dry_run(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Stale addon container
        stale_addon = MagicMock()
        stale_addon.name = "smsly-addon-postgres-c1b72ebf-1111-2222-3333-abcdef123456"
        stale_addon.id = "abcdef123456"

        # Stale green container
        stale_green = MagicMock()
        stale_green.name = "old-deleted-service-green-abc"
        stale_green.id = "deadbeef1234"

        mock_client.containers.list.return_value = [stale_addon, stale_green]

        out = io.StringIO()
        call_command('find_stale_runtime_resources', '--dry-run', stdout=out)
        output = out.getvalue()

        self.assertIn("Found 2 stale containers", output)
        self.assertIn("smsly-addon-postgres-c1b72ebf-1111-2222-3333-abcdef123456", output)
        self.assertIn("old-deleted-service-green-abc", output)
        stale_addon.stop.assert_not_called()
        stale_addon.remove.assert_not_called()

    @patch('apps.deployments.management.commands.find_stale_runtime_resources.docker.from_env')
    def test_find_stale_apply(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        stale_green = MagicMock()
        stale_green.name = "old-deleted-service-green-abc"
        stale_green.id = "deadbeef1234"

        mock_client.containers.list.return_value = [stale_green]

        out = io.StringIO()
        call_command('find_stale_runtime_resources', '--apply', stdout=out)
        output = out.getvalue()

        self.assertIn("Found 1 stale containers", output)
        self.assertIn("Removing old-deleted-service-green-abc", output)
        stale_green.stop.assert_called_once()
        stale_green.remove.assert_called_once_with(force=True)
