"""Unit tests for the deployment-log archival beat (ORM/S3 fully mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.models import Deployment
from apps.deployments.tasks.data.tasks_backup import archive_old_deployment_logs_task

TASK_PATH = "apps.deployments.tasks.data.tasks_backup"


def _row(build_len=300000, runtime_len=0, status="FAILED"):
    service = MagicMock(id="svc-1")
    service.name = "api"
    return MagicMock(
        id="dep-1", status=status,
        build_logs="x" * build_len, runtime_logs="y" * runtime_len,
        service=service,
    )


def _dest():
    d = MagicMock()
    d.bucket = "bkt"
    d.endpoint = ""
    d.region = "us-east-1"
    d.access_key = "ak"
    d.secret_key = "sk"
    return d


class TestArchiveOldDeploymentLogs(TestCase):
    def _run(self, rows, dest=None, upload_ok=True):
        mgr = MagicMock()
        (mgr.select_related.return_value.filter.return_value
            .order_by.return_value.__getitem__.return_value) = rows
        with patch("apps.deployments.models.Deployment.objects", mgr), \
             patch("apps.cloud.models.cloud_storage.CloudStorageDestination.objects.filter") as mock_dest_filter, \
             patch("apps.cloud.services.backup_service.upload_backup_to_s3") as mock_upload:
            mock_dest_filter.return_value.order_by.return_value.first.return_value = dest
            mock_upload.return_value = upload_ok
            res = archive_old_deployment_logs_task.run()
        return res, mock_upload

    def test_archives_and_truncates_with_destination(self):
        row = _row()
        res, mock_upload = self._run([row], dest=_dest())
        self.assertEqual(res["archived"], 1)
        self.assertEqual(res["errors"], 0)
        self.assertTrue(mock_upload.called)
        self.assertTrue(row.build_logs.startswith("[smsly-log-archive]"))
        self.assertIn("s3://bkt/smsly-deploy-logs/", row.build_logs)
        self.assertLessEqual(len(row.build_logs), 204800 + 500)
        row.save.assert_called_once()

    def test_truncates_without_destination(self):
        row = _row()
        res, mock_upload = self._run([row], dest=None)
        self.assertEqual(res, {"archived": 0, "truncated_only": 1, "skipped": 0, "errors": 0})
        mock_upload.assert_not_called()
        self.assertTrue(row.build_logs.startswith("[smsly-log-archive]"))
        self.assertIn("no archive destination", row.build_logs)
        row.save.assert_called_once()

    def test_failed_upload_leaves_row_untouched(self):
        row = _row()
        before = row.build_logs
        res, _ = self._run([row], dest=_dest(), upload_ok=False)
        self.assertEqual(res["errors"], 1)
        self.assertEqual(res["archived"], 0)
        self.assertEqual(row.build_logs, before)
        row.save.assert_not_called()

    def test_already_archived_row_skipped(self):
        row = _row()
        row.build_logs = "[smsly-log-archive] old marker\n..." + "x" * 300000
        res, mock_upload = self._run([row], dest=_dest())
        self.assertEqual(res["skipped"], 1)
        mock_upload.assert_not_called()
        row.save.assert_not_called()

    def test_small_logs_skipped(self):
        row = _row(build_len=100)
        res, _ = self._run([row], dest=_dest())
        self.assertEqual(res["skipped"], 1)
        row.save.assert_not_called()

    def test_task_name_matches_route_key(self):
        self.assertEqual(
            archive_old_deployment_logs_task.name,
            "apps.deployments.tasks_backup.archive_old_deployment_logs_task",
        )
