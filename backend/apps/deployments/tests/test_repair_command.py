import unittest
from unittest.mock import MagicMock, patch
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))

sys.modules["django"] = MagicMock()
sys.modules["django.db"] = MagicMock()
sys.modules['django.conf'] = MagicMock()
sys.modules['django.core.management.base'] = MagicMock()
sys.modules['apps.deployments.models'] = MagicMock()
sys.modules['apps.cloud.models'] = MagicMock()

class MockBaseCommand:
    def __init__(self):
        self.style = MagicMock()
        self.stdout = MagicMock()
sys.modules['django.core.management.base'].BaseCommand = MockBaseCommand

from apps.deployments.management.commands.repair_ecosystem_deploy import Command

class TestRepairCommand(unittest.TestCase):
    @patch('apps.deployments.management.commands.repair_ecosystem_deploy.Service')
    @patch('apps.deployments.management.commands.repair_ecosystem_deploy.Deployment')
    @patch('apps.deployments.management.commands.repair_ecosystem_deploy.select_eligible_node')
    @patch('apps.deployments.management.commands.repair_ecosystem_deploy.bulk_persist_and_verify_ecosystem_env')
    def test_dry_run(self, mock_env, mock_node, mock_deployment, mock_service):
        cmd = Command()
        mock_qs = MagicMock()
        mock_qs.exists.return_value = True
        mock_svc = MagicMock()
        mock_svc.name = "test-api"
        mock_svc.server = None
        mock_qs.first.return_value.owner = "user_1"
        mock_qs.__iter__.return_value = [mock_svc]
        mock_qs.count.return_value = 1
        mock_service.objects.filter.return_value = mock_qs

        cmd.handle(project="proj-123", dry_run=True, apply=False)

        mock_node.assert_not_called()
        mock_env.assert_not_called()
        cmd.stdout.write.assert_called()

if __name__ == '__main__':
    unittest.main()
