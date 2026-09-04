"""Verify the pre-restore snapshot failure is surfaced (Fix 4)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.deployments.models.backup import ServiceBackup
from apps.deployments.services.backup_service import BackupService

User = get_user_model()


class RestoreSnapshotRequiredTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="snap-user", password="x",
        )
        self.project = Project.objects.create(
            name="Snap Proj", owner=self.user,
        )
        self.service = Service.objects.create(
            name="snap-service",
            owner=self.user,
            project=self.project,
            public_domain="snap.example.com",
        )
        self.backup = ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path="/nonexistent/backup.tar.gz",
        )

    def test_restore_service_raises_when_snapshot_fails_and_flag_set(self):
        """The pre-restore snapshot is enforced by the VIEW (422), not by
        BackupService.restore_service — the service-level snapshot block and
        raise_on_snapshot_failure kwarg were removed when the gate moved into
        the viewset. The view-level behavior is covered by
        test_service_backup_viewset_returns_422_on_snapshot_failure; here we
        verify the service no longer takes a snapshot itself and proceeds
        straight to archive preparation (which raises for a missing file)."""
        with patch.object(
            BackupService, "backup_service",
            side_effect=RuntimeError("docker down"),
        ), self.assertRaises(FileNotFoundError):
            BackupService().restore_service(
                self.backup.id,
                requesting_user_id=self.user.id,
            )

    def test_restore_service_continues_when_snapshot_fails_by_default(self):
        with patch.object(
            BackupService, "backup_service",
            side_effect=RuntimeError("docker down"),
        ), patch.object(
            BackupService, "_prepare_archive_for_restore",
            side_effect=FileNotFoundError("Backup archive file not found."),
        ), self.assertRaises(FileNotFoundError):
            BackupService().restore_service(
                self.backup.id,
                requesting_user_id=self.user.id,
            )

    def test_service_backup_viewset_returns_422_on_snapshot_failure(self):
        from django.urls import reverse
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=self.user)

        url = reverse("backup-restore", args=[self.backup.id])
        with patch.object(
            BackupService, "backup_service",
            side_effect=RuntimeError("docker down"),
        ), patch(
            # The view binds the task name into its own module namespace
            # (from ...tasks import restore_service_backup_task), so patch
            # it where the view looks it up.
            "apps.deployments.views.backup.restore.restore_service_backup_task.delay",
        ) as mock_delay:
            response = client.post(
                url, {"confirm": "true"}, format="json",
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Pre-restore safety snapshot could not be created", response.data["error"])
        self.assertEqual(response.data["snapshot_error"], "docker down")
        mock_delay.assert_not_called()
