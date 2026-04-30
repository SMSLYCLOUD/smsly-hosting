import io
from django.test import TestCase
from django.core.management import call_command
from apps.deployments.models_core import Service
from apps.deployments.models_addons import Addon
from unittest.mock import patch, MagicMock

class TestReconcileCommand(TestCase):
    @patch('apps.deployments.management.commands.reconcile_runtime_resources.docker.from_env')
    @patch('apps.deployments.management.commands.reconcile_runtime_resources.DeletionOrchestrator')
    def test_reconcile_dry_run(self, mock_orchestrator, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        mock_orch_instance = MagicMock()
        mock_orchestrator.return_value = mock_orch_instance

        # Orphan container matching an ID that isn't in DB
        orphan_container = MagicMock()
        orphan_container.labels = {'smsly.managed': 'true', 'smsly.service_id': 'abc-123'}
        orphan_container.name = "test-service"
        orphan_container.id = "123"

        mock_client.containers.list.return_value = [orphan_container]

        out = io.StringIO()
        call_command('reconcile_runtime_resources', '--dry-run', stdout=out)
        output = out.getvalue()

        self.assertIn("Found 1 orphaned containers", output)
        self.assertIn("Service abc-123 deleted in DB", output)
        mock_orch_instance._safe_remove_container.assert_not_called()

    @patch('apps.deployments.management.commands.reconcile_runtime_resources.docker.from_env')
    @patch('apps.deployments.management.commands.reconcile_runtime_resources.DeletionOrchestrator')
    def test_reconcile_apply(self, mock_orchestrator, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        mock_orch_instance = MagicMock()
        mock_orchestrator.return_value = mock_orch_instance

        orphan_container = MagicMock()
        orphan_container.labels = {'smsly.managed': 'true', 'smsly.service_id': 'abc-123'}
        orphan_container.name = "test-service"
        orphan_container.id = "123"

        mock_client.containers.list.return_value = [orphan_container]

        out = io.StringIO()
        call_command('reconcile_runtime_resources', '--apply', stdout=out)

        mock_orch_instance._safe_remove_container.assert_called_once_with(orphan_container)
