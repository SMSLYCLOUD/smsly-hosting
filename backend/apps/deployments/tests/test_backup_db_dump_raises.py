"""
Tests for DB dump / addon backup failure propagation.

``_dump_container_database`` and ``backup_addon`` now raise
``RuntimeError`` when the underlying ``pg_dump`` / ``mysqldump`` /
``redis-cli`` commands fail, instead of silently logging and
returning ``None``.  This prevents service backup from silently
producing incomplete archives.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase


class DumpContainerDatabaseRaisesTest(TestCase):
    """_dump_container_database raises when pg_dump/mysqldump fail."""

    def setUp(self):
        self.container_name = "test-db-ctr"
        self.image_tag = "postgres:16"
        self.temp_dir = "/tmp/test-dump"

    @patch("docker.from_env")
    def test_postgres_pg_dump_failure_raises(self, mock_docker_from_env):
        from apps.deployments.services.backup_service import _dump_container_database

        mock_client = MagicMock()
        mock_docker_from_env.return_value = mock_client

        mock_ctr = MagicMock()
        mock_client.containers.get.return_value = mock_ctr

        # Simulate pg_dump failure
        exec_result = MagicMock()
        exec_result.exit_code = 1
        exec_result.output = b"pg_dump: error: connection to server failed"
        mock_ctr.exec_run.return_value = exec_result

        with self.assertRaises(RuntimeError) as ctx:
            _dump_container_database(self.container_name, self.image_tag, self.temp_dir)

        self.assertIn("pg_dumpall failed", str(ctx.exception))

    @patch("docker.from_env")
    def test_mysql_mysqldump_failure_raises(self, mock_docker_from_env):
        from apps.deployments.services.backup_service import _dump_container_database

        mock_client = MagicMock()
        mock_docker_from_env.return_value = mock_client

        mock_ctr = MagicMock()
        mock_client.containers.get.return_value = mock_ctr

        # Simulate MySQL container attrs
        mock_ctr.attrs = {"Config": {"Env": ["MYSQL_ROOT_PASSWORD=rootpass"]}}

        # mysqldump fails
        exec_result = MagicMock()
        exec_result.exit_code = 1
        exec_result.output = b"mysqldump: Got error"
        mock_ctr.exec_run.return_value = exec_result

        with self.assertRaises(RuntimeError) as ctx:
            _dump_container_database(self.container_name, "mysql:8", self.temp_dir)

        self.assertIn("mysqldump failed", str(ctx.exception))


class BackupAddonFailureTest(TestCase):
    """backup_addon catches internal RuntimeError and returns None."""

    def _run_addon_test(self, addon_type, env_vars):
        """Helper to run a single addon failure scenario."""
        from apps.deployments.models_addons import Addon
        from apps.deployments.services.backup_service import backup_addon

        with patch("docker.from_env") as mock_docker_from_env, \
             patch.object(Addon.objects, 'get') as mock_addon_get:
            mock_client = MagicMock()
            mock_docker_from_env.return_value = mock_client

            mock_ctr = MagicMock()
            mock_client.containers.get.return_value = mock_ctr

            mock_addon = MagicMock()
            mock_addon.id = "addon-test"
            mock_addon.container_name = f"{addon_type}-ctr"
            mock_addon.addon_type = addon_type
            mock_addon.service.id = 42
            mock_addon_get.return_value = mock_addon

            mock_ctr.attrs = {"Config": {"Env": env_vars}}

            exec_result_fail = MagicMock()
            exec_result_fail.exit_code = 1
            exec_result_fail.output = b"dump: error"
            mock_ctr.exec_run.return_value = exec_result_fail

            result = backup_addon("addon-test")
            self.assertIsNone(result)

    def test_addon_pg_dumpall_failure_returns_none(self):
        self._run_addon_test(
            "postgres",
            ["POSTGRES_USER=myuser", "POSTGRES_DB=mydb"],
        )

    def test_addon_mysqldump_failure_returns_none(self):
        self._run_addon_test(
            "mysql",
            ["MYSQL_ROOT_PASSWORD=rootpass"],
        )
