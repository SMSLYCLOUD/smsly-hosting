from django.test import TestCase
from unittest.mock import patch, MagicMock
from apps.deployments.services.ecosystem_persist import bulk_persist_and_verify_ecosystem_env

class TestEcosystemPersistSafe(TestCase):
    @patch('apps.deployments.services.ecosystem_persist.EnvironmentVariable.objects')
    def test_persist_success(self, mock_env_objects):
        manifest_yaml = """
        version: "1"
        mode: "production"
        services:
          api:
            env:
              API_KEY:
                source: generated
        """
        mock_service = MagicMock()
        mock_env_objects.filter.return_value.count.return_value = 1

        success, msg = bulk_persist_and_verify_ecosystem_env(manifest_yaml, {"api": mock_service})
        self.assertTrue(success)
        self.assertEqual(msg, "")
        mock_env_objects.update_or_create.assert_called()

    def test_persist_failure_missing_req(self):
        manifest_yaml = """
        version: "1"
        mode: "production"
        services:
          api:
            env:
              EXTERNAL_KEY:
                source: external_required
                required: true
        """
        success, msg = bulk_persist_and_verify_ecosystem_env(manifest_yaml, {"api": MagicMock()})
        self.assertFalse(success)
        self.assertIn("missing external required env", msg)

    @patch('apps.deployments.services.ecosystem_persist.EnvironmentVariable.objects')
    def test_invalid_plan_rejected(self, mock_env_objects):
        # Invalid circular dependency
        manifest_yaml = """
        version: "1"
        services:
          api:
            dependencies: [worker]
          worker:
            dependencies: [api]
        """
        mock_api = MagicMock()
        mock_worker = MagicMock()
        success, msg = bulk_persist_and_verify_ecosystem_env(manifest_yaml, {"api": mock_api, "worker": mock_worker})
        self.assertFalse(success)
        self.assertIn("Invalid dependency graph", msg)

    @patch('apps.deployments.services.ecosystem_persist.EnvironmentVariable.objects')
    def test_missing_target_service_rejected(self, mock_env_objects):
        manifest_yaml = """
        version: "1"
        services:
          api:
            type: backend
        """
        success, msg = bulk_persist_and_verify_ecosystem_env(manifest_yaml, {})
        self.assertFalse(success)
        self.assertIn("missing from creation map", msg)
