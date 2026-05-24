import unittest
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
from apps.deployments.services.safedeploy.branch_preview_manager import BranchPreviewManager
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
from apps.deployments.models_core import Service, EnvironmentVariable, Deployment
from apps.deployments.models_safedeploy import PreviewEnvironment
from apps.deployments.tasks_safedeploy import _make_clone_database_name, run_preview_health_check_job
from apps.cloud.models import CloudProvider

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


class SafeDeployTaskHelpersTestCase(unittest.TestCase):
    def test_clone_database_name_is_valid_and_within_postgres_limit(self):
        name = _make_clone_database_name(
            "customer-db",
            "feature/some very long branch name with invalid chars!!!! and more",
            "abcdef1234567890",
        )

        self.assertLessEqual(len(name), 63)
        self.assertRegex(name, r"^[A-Za-z0-9_]+$")
        self.assertTrue(name.startswith("preview_"))


class BranchPreviewManagerTestCase(TestCase):
    def test_create_preview_urls_are_unique_for_same_branch(self):
        user = User.objects.create_user(username="preview-url-user", password="p")
        service = Service.objects.create(name="preview-url-service", owner=user)
        manager = BranchPreviewManager()

        first = manager.create_preview(service, "feature/same", "a" * 7, user=user)
        second = manager.create_preview(service, "feature/same", "b" * 7, user=user)

        self.assertNotEqual(first.preview_url, second.preview_url)


class PreviewHealthJobTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="preview-job-user", password="p")
        self.provider = CloudProvider.objects.create(
            name="preview-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.parent = Service.objects.create(
            name="preview-parent-service",
            owner=self.user,
            provider=self.provider,
            repository_url="https://github.com/test/app",
            branch="main",
            build_command="npm run build",
            start_command="npm start",
        )
        EnvironmentVariable.objects.create(
            service=self.parent,
            key="DATABASE_URL",
            value="postgres://prod/prod",
            is_secret=True,
            source="USER",
        )
        self.preview = PreviewEnvironment.objects.create(
            service=self.parent,
            branch_name="feature/preview",
            commit_sha="a" * 7,
            preview_url="https://preview.example.com",
            status=PreviewEnvironment.Status.HEALTH_CHECK_RUNNING,
        )

    @patch("apps.deployments.tasks_safedeploy._dispatch_preview_deployment")
    @patch("apps.deployments.tasks_safedeploy._sync_preview_addons")
    def test_health_job_syncs_preview_service_and_dispatches_each_run(self, mock_sync_addons, mock_dispatch):
        run_preview_health_check_job(str(self.preview.id))

        transient = Service.objects.get(parent_service=self.parent, is_preview=True)
        self.assertEqual(transient.repository_url, self.parent.repository_url)
        self.assertEqual(transient.branch, self.preview.branch_name)
        self.assertEqual(transient.public_domain, "preview.example.com")
        self.assertEqual(mock_sync_addons.call_count, 1)
        self.assertEqual(mock_dispatch.call_count, 1)
        self.assertEqual(Deployment.objects.filter(service=transient).count(), 1)
        copied_db_url = EnvironmentVariable.objects.get(service=transient, key="DATABASE_URL")
        self.assertEqual(copied_db_url.value, "postgres://prod/prod")

        self.preview.commit_sha = "b" * 7
        self.preview.save(update_fields=["commit_sha"])
        run_preview_health_check_job(str(self.preview.id))

        self.assertEqual(mock_sync_addons.call_count, 2)
        self.assertEqual(mock_dispatch.call_count, 2)
        self.assertEqual(Deployment.objects.filter(service=transient).count(), 2)
