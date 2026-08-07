# pylint: disable=invalid-name
"""Regression test: ensure the addon backup download response closes its
underlying file handle so that we do not leak file descriptors on every
download.
"""
import os
import tempfile
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon, Backup
from apps.addons.views.crud import _ClosingFileResponse

User = get_user_model()


def _make_user(username='fdltest'):
    return User.objects.create_user(
        username=username,
        email=f'{username}@example.com',
        password='testpass123',
    )


def _make_service(user):
    provider = CloudProvider.objects.create(
        name=f'provider-{uuid.uuid4().hex[:8]}',
        provider_type='LOCAL',
        is_active=True,
    )
    return Service.objects.create(
        name=f'svc-{uuid.uuid4().hex[:8]}',
        repository_url='https://github.com/test/app',
        owner=user,
        provider=provider,
    )


class ClosingFileResponseCloseTests(TestCase):
    """The custom FileResponse subclass must close its file on close()."""

    def test_close_closes_underlying_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b'hello world')
            temp_path = tf.name
        try:
            f = open(temp_path, 'rb')
            response = _ClosingFileResponse(f, as_attachment=True,
                                            filename='x.bin')
            try:
                self.assertFalse(f.closed)
                self.assertFalse(response._file.closed)
                response.close()
                self.assertTrue(f.closed)
            finally:
                if not f.closed:
                    f.close()
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_close_idempotent_when_already_closed(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b'x')
            temp_path = tf.name
        try:
            f = open(temp_path, 'rb')
            response = _ClosingFileResponse(f, as_attachment=True,
                                            filename='x.bin')
            f.close()
            # Must not raise even when the file is already closed.
            response.close()
            response.close()
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class AddonDownloadBackupFileHandleTests(TestCase):
    """End-to-end: the download_backup action must not leak the file handle."""

    def setUp(self):
        self.user = _make_user('fdl_e2e')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.service = _make_service(self.user)
        self.addon = Addon.objects.create(
            service=self.service,
            name=f'addon-{uuid.uuid4().hex[:8]}',
            addon_type='POSTGRES',
        )

    def test_download_backup_closes_file_handle(self):
        backups_dir = os.path.realpath(
            os.path.join(settings.BASE_DIR, 'backups')
        )
        os.makedirs(backups_dir, exist_ok=True)
        fname = f'fixture-{uuid.uuid4().hex}.bin'
        full_path = os.path.join(backups_dir, fname)
        with open(full_path, 'wb') as out:
            out.write(b'arbitrary backup payload')
        backup = Backup.objects.create(
            addon=self.addon,
            file_path=full_path,
            size_bytes=len(b'arbitrary backup payload'),
            status=Backup.Status.COMPLETED,
        )
        try:
            url = (
                f'/api/v1/addons/{self.addon.id}/download_backup/'
                f'?backup_id={backup.id}'
            )

            # Wrap builtins.open so we can capture the file object the view
            # opens for the response and assert it is closed afterwards.
            import builtins
            opened_files = []
            builtin_open = builtins.open

            def wrapped_open(file, mode='r', *args, **kwargs):
                f = builtin_open(file, mode, *args, **kwargs)
                opened_files.append(f)
                return f

            builtins.open = wrapped_open
            try:
                response = self.client.get(url)
                # Django's test client does not call .close() on the
                # response. Force it so the FileResponse.close() hook runs
                # and releases the underlying file handle.
                try:
                    response.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            finally:
                builtins.open = builtin_open

            self.assertEqual(response.status_code, 200)
            self.assertTrue(opened_files, 'expected the view to open a file')
            for f in opened_files:
                self.assertTrue(
                    f.closed,
                    f'File handle was not closed by the response: {f!r}',
                )
        finally:
            if os.path.exists(full_path):
                os.unlink(full_path)
