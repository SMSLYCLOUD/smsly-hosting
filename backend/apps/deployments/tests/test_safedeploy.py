import unittest
from unittest.mock import patch
from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
from apps.deployments.models_safedeploy import MigrationValidation

class DjangoAdapterTestCase(unittest.TestCase):
    def test_classify_migration_risk_critical(self):
        adapter = DjangoAdapter()
        operations = [
            {'type': 'DeleteModel', 'file': '0002_delete_user.py'},
            {'type': 'AddField', 'file': '0003_add_field.py'}
        ]
        report = adapter.classify_migration_risk(operations)

        self.assertEqual(report['risk_level'], MigrationValidation.RiskLevel.CRITICAL)
        self.assertTrue(report['requires_manual_approval'])
        self.assertTrue(report['requires_backup'])
        self.assertFalse(report['can_auto_deploy'])
        self.assertEqual(report['risk_score'], 100)

    def test_classify_migration_risk_low(self):
        adapter = DjangoAdapter()
        operations = [
            {'type': 'AddField', 'file': '0003_add_field.py'}
        ]
        report = adapter.classify_migration_risk(operations)

        self.assertEqual(report['risk_level'], MigrationValidation.RiskLevel.LOW)
        self.assertFalse(report['requires_manual_approval'])
        self.assertFalse(report['requires_backup'])
        self.assertTrue(report['can_auto_deploy'])
        self.assertEqual(report['risk_score'], 0)

from apps.deployments.services.safedeploy.redaction import redact_secrets

class RedactionTestCase(unittest.TestCase):
    def test_redact_secrets(self):
        text = "Connecting to DATABASE_URL=postgres://user:password@localhost/db"
        redacted = redact_secrets(text)
        self.assertIn("[REDACTED]", redacted)
        self.assertNotIn("postgres://user:password@localhost/db", redacted)

        text2 = "API_KEY=sk-test-12345"
        redacted2 = redact_secrets(text2)
        self.assertIn("[REDACTED]", redacted2)
        self.assertNotIn("sk-test-12345", redacted2)

from apps.deployments.services.safedeploy.postgres_snapshot_manager import PostgresSnapshotManager

class PostgresSnapshotManagerTestCase(unittest.TestCase):
    @patch('subprocess.run')
    def test_guardrails_on_destroy(self, mock_run):
        manager = PostgresSnapshotManager(admin_db_url="postgres://user:pass@localhost:5432/postgres")

        # Should be allowed
        res = manager.destroy_clone("preview_1234_myapp")
        self.assertTrue(res)
        self.assertEqual(mock_run.call_count, 2)

        mock_run.reset_mock()

        # Should be blocked
        res2 = manager.destroy_clone("myapp_prod_db")
        self.assertFalse(res2)
        self.assertEqual(mock_run.call_count, 0)

        res3 = manager.destroy_clone("myapp_main_db")
        self.assertFalse(res3)
        self.assertEqual(mock_run.call_count, 0)
