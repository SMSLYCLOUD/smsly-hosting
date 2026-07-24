from collections import namedtuple
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.storage import Volume
from apps.deployments.views.storage import VolumeViewSet

User = get_user_model()
_UsageTuple = namedtuple('_UsageTuple', ['total', 'used', 'free'])


def _make_container_with_archive():
    container = MagicMock()
    container.exec_run.return_value = (0, b'')
    container.put_archive.return_value = True
    return container


class Finding97DiskSpaceCheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fix97', password='x')
        self.service = Service.objects.create(name='fix97-svc', owner=self.user)
        self.volume = Volume.objects.create(
            service=self.service,
            name='fix97-volume',
            mount_path='/data',
            size_gb=5,
        )
        self.viewset = VolumeViewSet()

    def test_rejects_write_when_content_exceeds_ninety_percent_free(self):
        container = _make_container_with_archive()
        with patch(
            'apps.deployments.views.storage.shutil.disk_usage',
            return_value=_UsageTuple(total=1024, used=24, free=1000),
        ):
            response = self.viewset._local_volume_file_write(
                container,
                '/data/big.txt',
                'A' * 1500,
                volume=self.volume,
            )
        self.assertEqual(response.status_code, 507)
        self.assertIn('Insufficient disk space', str(response.data.get('error', '')))
        container.put_archive.assert_not_called()

    def test_accepts_write_when_content_fits_within_threshold(self):
        container = _make_container_with_archive()
        with patch(
            'apps.deployments.views.storage.shutil.disk_usage',
            return_value=_UsageTuple(total=10_000_000, used=0, free=10_000_000),
        ):
            response = self.viewset._local_volume_file_write(
                container,
                '/data/tiny.txt',
                'hello world',
                volume=self.volume,
            )
        self.assertEqual(response.status_code, 200)
        container.put_archive.assert_called_once()

    def test_falls_back_to_root_when_mount_path_missing_on_host(self):
        container = _make_container_with_archive()
        called_with = {}

        def _fake_disk_usage(path):
            called_with['path'] = path
            return _UsageTuple(total=10_000, used=0, free=10_000)

        with patch(
            'apps.deployments.views.storage.shutil.disk_usage',
            side_effect=_fake_disk_usage,
        ):
            self.viewset._local_volume_file_write(
                container,
                '/data/anywhere.txt',
                'ok',
                volume=self.volume,
            )
        self.assertEqual(called_with['path'], '/')

    def test_skips_check_silently_when_disk_usage_raises(self):
        container = _make_container_with_archive()
        with patch(
            'apps.deployments.views.storage.shutil.disk_usage',
            side_effect=OSError('not supported'),
        ):
            response = self.viewset._local_volume_file_write(
                container,
                '/data/ok.txt',
                'still works',
                volume=self.volume,
            )
        self.assertEqual(response.status_code, 200)
        container.put_archive.assert_called_once()
