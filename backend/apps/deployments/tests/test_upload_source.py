# pylint: disable=invalid-name
"""Tests for SEC (Issue 43): upload_source zip-slip and tenant isolation."""
import io
import os
import tempfile
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service

User = get_user_model()


def _make_zip(entries):
    """Return bytes for a zip with the given [(name, content), ...]."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries:
            zf.writestr(name, content)
    return buf.getvalue()


class UploadSourceSecurityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="uploadsec",
            email="uploadsec@example.com",
            password="pw",
        )
        self.client.force_authenticate(user=self.user)
        self.provider = CloudProvider.objects.create(
            name="uploadsec-prov",
            provider_type="LOCAL",
            is_active=True,
        )
        self.service = Service.objects.create(
            name="uploadsec-svc",
            repository_url="https://github.com/x/y",
            owner=self.user,
            provider=self.provider,
        )
        self.url = "/api/v1/deployments/upload/"
        self.tmpdir = tempfile.mkdtemp(prefix="uploadsec-")
        # Redirect /app/var/uploads to the test temp dir so the test
        # works on any OS.
        self._orig_join = os.path.join
        self._orig_abspath = os.path.abspath
        os.path.join = self._join_redirect
        os.path.abspath = self._abspath_redirect

    def tearDown(self):
        os.path.join = self._orig_join
        os.path.abspath = self._orig_abspath
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _join_redirect(self, *args):
        if args and args[0] == "/app/var/uploads":
            return os.path.join(self.tmpdir, *args[1:])
        return self._orig_join(*args)

    def _abspath_redirect(self, p):
        if p == "/app/var/uploads":
            return self.tmpdir
        return self._orig_abspath(p)

    def _post(self, file_bytes, name="bundle.zip"):
        upload = SimpleUploadedFile(name, file_bytes, content_type="application/zip")
        return self.client.post(
            self.url,
            {"service_id": str(self.service.id), "file": upload},
            format="multipart",
        )

    def _post_with_provider(self, file_bytes, name="bundle.zip"):
        with patch("apps.deployments.views.service.deploy.smart_deploy_task.delay"), \
                patch("apps.deployments.views._resolve_provider_for_service",
                      return_value=self.provider):
            return self._post(file_bytes, name)

    def test_happy_path_writes_to_tenant_upload_dir(self):
        """A normal zip lands in a per-tenant directory and updates the service."""
        response = self._post_with_provider(
            _make_zip([("app.py", b"print('hi')")])
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.service.refresh_from_db()
        # The tenant id (== self.user.id as string) should appear in the path
        self.assertIn(str(self.user.id), self.service.repository_url)
        # And the URL must point inside the test temp dir
        self.assertTrue(
            self.service.repository_url.startswith("file://"),
            self.service.repository_url,
        )
        # A file should have been written inside the tenant dir
        tenant_dir = os.path.join(self.tmpdir, str(self.user.id))
        self.assertTrue(os.path.isdir(tenant_dir))
        zip_files = [f for f in os.listdir(tenant_dir) if f.endswith(".zip")]
        self.assertTrue(zip_files, f"No zip files in {tenant_dir}")

    def test_zip_slip_is_rejected(self):
        """A zip with a path-traversal entry is rejected and the file removed."""
        response = self._post_with_provider(
            _make_zip([("../../etc/passwd", b"pwned")])
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("unsafe entries", response.data.get("error", ""))
        # Service must NOT have been updated to the malicious URL
        self.service.refresh_from_db()
        self.assertFalse(
            (self.service.repository_url or "").startswith("file://"),
            self.service.repository_url,
        )
        # The malicious zip must not be left on disk
        tenant_dir = os.path.join(self.tmpdir, str(self.user.id))
        if os.path.isdir(tenant_dir):
            zip_files = [f for f in os.listdir(tenant_dir) if f.endswith(".zip")]
            self.assertEqual(zip_files, [], f"Leftover zip files: {zip_files}")

    def test_invalid_zip_is_rejected(self):
        """A non-zip payload is rejected and removed."""
        response = self._post_with_provider(b"NOT_A_ZIP")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid zip", response.data.get("error", ""))

    def test_size_limit_enforced(self):
        """Files larger than 100MB are rejected with 413."""
        big_zip = b"PK\x03\x04" + b"x" * (101 * 1024 * 1024)
        response = self._post(big_zip)
        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    def test_non_zip_extension_rejected(self):
        """Files that don't end with .zip are rejected."""
        upload = SimpleUploadedFile(
            "malicious.exe", b"fake", content_type="application/octet-stream"
        )
        response = self.client.post(
            self.url,
            {"service_id": str(self.service.id), "file": upload},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid file type", response.data.get("error", ""))
