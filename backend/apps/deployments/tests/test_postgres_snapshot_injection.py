from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.deployments.services.safedeploy.postgres_snapshot_manager import (
    PostgresSnapshotManager,
    _validate_db_name,
)


class DbNameValidationTests(TestCase):
    def test_valid_names_pass(self):
        for name in ("myapp", "_private", "a1", "clone_db_2"):
            _validate_db_name(name)

    def test_invalid_names_raise(self):
        for name in ("1starts_with_digit", "bad;name", "bad name", "bad-name",
                     "DROP TABLE", "with space", ""):
            with self.assertRaises(ValueError):
                _validate_db_name(name)


class PostgresSnapshotManagerParameterizationTests(TestCase):
    def setUp(self):
        self.mgr = PostgresSnapshotManager(
            admin_db_url="postgres://u:p@db:5432/admin",
        )

    @patch("apps.deployments.services.safedeploy.postgres_snapshot_manager.subprocess.run")
    def test_create_clone_uses_psql_vars(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.mgr.create_clone("sourcedb", "clonedb", allow_production_disruption=True)
        self.assertTrue(mock_run.called)
        flat_cmd = []
        for call in mock_run.call_args_list:
            flat_cmd.extend(call.args[0])
        cmd_str = " ".join(flat_cmd)
        self.assertIn("-v", cmd_str)
        self.assertIn("source_db=sourcedb", cmd_str)

    @patch("apps.deployments.services.safedeploy.postgres_snapshot_manager.subprocess.run")
    def test_destroy_clone_uses_psql_vars(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        self.mgr.destroy_clone("clonedb")
        self.assertTrue(mock_run.called)
        flat_cmd = []
        for call in mock_run.call_args_list:
            flat_cmd.extend(call.args[0])
        cmd_str = " ".join(flat_cmd)
        self.assertIn("clone_db=clonedb", cmd_str)
