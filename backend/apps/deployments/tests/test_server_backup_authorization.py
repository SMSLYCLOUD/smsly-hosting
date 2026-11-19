"""Authorization regressions for server backup download actions."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.backup import ServerBackup

User = get_user_model()


class ServerBackupDownloadAuthorizationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="backup-admin", password="x", email="backup-admin@example.test"
        )
        self.staff = User.objects.create_user(
            username="backup-staff", password="x", is_staff=True
        )
        self.user = User.objects.create_user(username="backup-user", password="x")
        self.backup = ServerBackup.objects.create(
            status="COMPLETED", file_path="/review/server-backup.tar.gz", size_bytes=4
        )
        self.client = APIClient()

    def _url(self, action="download", pk=None):
        from django.urls import reverse

        return reverse(f"server-backup-{action}", args=[pk or self.backup.id])

    def test_nonstaff_cannot_download_or_download_key(self):
        self.client.force_authenticate(user=self.user)

        download = self.client.get(self._url())
        key = self.client.get(self._url("download-key"))

        self.assertEqual(download.status_code, 403)
        self.assertEqual(key.status_code, 403)

    @patch("apps.deployments.views.server_backup._open_backup_download_response")
    @patch("apps.deployments.views.server_backup.os.path.exists", return_value=True)
    def test_superuser_can_download(self, _exists, open_response):
        open_response.return_value = HttpResponse(b"backup")
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        open_response.assert_called_once()

    def test_unknown_uuid_returns_not_found_for_admin(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self._url(pk="00000000-0000-0000-0000-000000000000"))

        self.assertEqual(response.status_code, 404)

    def test_signed_link_does_not_bypass_admin_requirement(self):
        anon = self.client.get(self._url() + "?signed=valid-capability")
        self.assertIn(anon.status_code, (401, 403))

        self.client.force_authenticate(user=self.user)
        non_staff = self.client.get(self._url() + "?signed=valid-capability")
        self.assertEqual(non_staff.status_code, 403)

    @patch("apps.deployments.views.server_backup.os.path.exists", return_value=True)
    @patch("apps.deployments.services.backup_service.BackupService.read_v2_header")
    def test_superuser_can_download_key_metadata(self, read_header, _exists):
        read_header.return_value = {"format": "CHUNKED_V2", "key_id": "key-1", "fingerprint": "fp"}
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self._url("download-key"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"key-1", response.content)

    @patch("apps.deployments.views.server_backup._open_backup_download_response")
    @patch("apps.deployments.views.server_backup.os.path.exists", return_value=True)
    def test_staff_can_download_and_download_key_but_cannot_import_key(self, _exists, open_response):
        from django.urls import reverse

        open_response.return_value = HttpResponse(b"backup")
        self.client.force_authenticate(user=self.staff)

        dl = self.client.get(self._url())
        self.assertEqual(dl.status_code, 200)
        open_response.assert_called_once()

        with patch(
            "apps.deployments.services.backup_service.BackupService.read_v2_header",
            return_value={"format": "CHUNKED_V2", "key_id": "key-staff", "fingerprint": "fp"},
        ):
            dk = self.client.get(self._url("download-key"))
        self.assertEqual(dk.status_code, 200)
        self.assertIn(b"key-staff", dk.content)

        imp = self.client.post(
            reverse("server-backup-import-key"),
            {"key_id": "k1", "key_material": "mat"},
            format="json",
        )
        self.assertEqual(imp.status_code, 403)

    def test_non_uuid_pk_returns_not_found_for_admin(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.get(self._url(pk="not-a-uuid"))

        self.assertEqual(response.status_code, 404)

    def test_cloud_uploaded_with_empty_file_path_returns_not_found(self):
        self.client.force_authenticate(user=self.admin)
        cloud_backup = ServerBackup.objects.create(
            status="COMPLETED",
            file_path="",
            cloud_uploaded=True,
            size_bytes=4,
        )

        response = self.client.get(self._url(pk=cloud_backup.id))

        self.assertEqual(response.status_code, 404)
