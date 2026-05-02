import unittest
from unittest.mock import MagicMock, patch

from apps.deployments.services.ecosystem_persist import bulk_persist_and_verify_ecosystem_env

class TestEcosystemPersist(unittest.TestCase):
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

if __name__ == '__main__':
    unittest.main()
