"""Tests that backup download endpoints only accept `?signed=`, not `?token=`."""
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service
from apps.deployments.models.backup import ServerBackup, ServiceBackup

User = get_user_model()


def _make_signed(pk: str) -> str:
    return signing.TimestampSigner().sign_object({'pk': str(pk), 'ts': 0})


def _write_file(payload: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix='.tar.gz')
    with os.fdopen(fd, 'wb') as f:
        f.write(payload)
    return path


class ServiceBackupDownloadSignedOnlyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='svc-owner', password='x',
        )
        self.other = User.objects.create_user(
            username='svc-other', password='x',
        )
        self.project = Project.objects.create(name='P', owner=self.owner)
        self.service = Service.objects.create(
            name='svc', owner=self.owner, project=self.project,
        )
        self.payload = b'service-backup-payload'
        self.path = _write_file(self.payload)
        self.backup = ServiceBackup.objects.create(
            service=self.service,
            status='COMPLETED',
            file_path=self.path,
            size_bytes=len(self.payload),
        )
        self.token = Token.objects.create(user=self.owner)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.client_no_auth = APIClient()

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _download(self, qs: str, client=None):
        client = client or self.client_no_auth
        from django.urls import reverse
        return client.get(
            reverse('backup-download', args=[self.backup.id]) + qs,
        )

    def test_token_only_returns_401(self):
        response = self._download(f'?token={self.token.key}')
        self.assertEqual(response.status_code, 401)

    def test_signed_only_returns_200_with_file_content(self):
        signed = _make_signed(str(self.backup.id))
        response = self._download(f'?signed={signed}', client=self.client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), self.payload)

    def test_no_auth_param_returns_401(self):
        response = self._download('')
        self.assertEqual(response.status_code, 401)

    def test_mixed_token_and_signed_returns_401(self):
        signed = _make_signed(str(self.backup.id))
        response = self._download(
            f'?token={self.token.key}&signed={signed}',
        )
        self.assertEqual(response.status_code, 401)


class ServerBackupDownloadSignedOnlyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='srv-admin', password='x',
        )
        self.payload = b'server-backup-payload'
        self.path = _write_file(self.payload)
        self.backup = ServerBackup.objects.create(
            status='COMPLETED',
            file_path=self.path,
            size_bytes=len(self.payload),
        )
        self.token = Token.objects.create(user=self.admin)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.client_no_auth = APIClient()

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _download(self, qs: str, client=None):
        client = client or self.client_no_auth
        from django.urls import reverse
        return client.get(
            reverse('server-backup-download', args=[self.backup.id]) + qs,
        )

    def test_token_only_returns_401(self):
        response = self._download(f'?token={self.token.key}')
        self.assertEqual(response.status_code, 401)

    def test_signed_only_returns_200_with_file_content(self):
        signed = _make_signed(str(self.backup.id))
        response = self._download(f'?signed={signed}', client=self.client)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), self.payload)

    def test_no_auth_param_returns_401(self):
        response = self._download('')
        self.assertEqual(response.status_code, 401)

    def test_mixed_token_and_signed_returns_401(self):
        signed = _make_signed(str(self.backup.id))
        response = self._download(
            f'?token={self.token.key}&signed={signed}',
        )
        self.assertEqual(response.status_code, 401)
