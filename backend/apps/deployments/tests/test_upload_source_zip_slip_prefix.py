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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries:
            zf.writestr(name, content)
    return buf.getvalue()


class UploadSourceZipSlipPrefixTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="zipslip-prefix",
            email="zs@example.com",
            password="pw",
        )
        self.client.force_authenticate(user=self.user)
        self.provider = CloudProvider.objects.create(
            name="zipslip-prov",
            provider_type="LOCAL",
            is_active=True,
        )
        self.service = Service.objects.create(
            name="zipslip-svc",
            repository_url="https://github.com/x/y",
            owner=self.user,
            provider=self.provider,
        )
        self.url = "/api/v1/deployments/upload/"
        self.tmpdir = tempfile.mkdtemp(prefix="zipslip-")
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

    def _post(self, file_bytes):
        upload = SimpleUploadedFile("bundle.zip", file_bytes, content_type="application/zip")
        with patch("apps.deployments.views.service.deploy.smart_deploy_task.delay"), \
                patch("apps.deployments.views._resolve_provider_for_service",
                      return_value=self.provider):
            return self.client.post(
                self.url,
                {"service_id": str(self.service.id), "file": upload},
                format="multipart",
            )

    def test_absolute_path_entry_rejected(self):
        response = self._post(_make_zip([("/etc/passwd", b"x")]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("unsafe entries", response.data.get("error", ""))

    def test_dotdot_segment_entry_rejected(self):
        response = self._post(_make_zip([("foo/../../bar", b"x")]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("unsafe entries", response.data.get("error", ""))

    def test_normal_path_entry_accepted(self):
        response = self._post(_make_zip([("src/app.py", b"x")]))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
