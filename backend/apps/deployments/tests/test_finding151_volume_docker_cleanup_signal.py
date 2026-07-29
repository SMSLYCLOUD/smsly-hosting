from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.storage import Volume

User = get_user_model()


class Finding151ServiceVolumeDockerCleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fix151', password='x')
        self.service = Service.objects.create(name='fix151-svc', owner=self.user)
        self.vol_a = Volume.objects.create(
            service=self.service, name='fix151_vol_a', mount_path='/data/a', size_gb=1,
        )
        self.vol_b = Volume.objects.create(
            service=self.service, name='fix151_vol_b', mount_path='/data/b', size_gb=1,
        )

    def test_post_delete_calls_docker_volume_remove(self):
        fake_volume = MagicMock()
        fake_volume.remove = MagicMock()
        fake_client = MagicMock()
        fake_client.volumes.get = MagicMock(return_value=fake_volume)

        fake_docker = MagicMock()
        fake_docker.from_env = MagicMock(return_value=fake_client)

        with patch.dict('sys.modules', {'docker': fake_docker}):
            self.service.delete()

        get_call_names = [
            call.args[0] for call in fake_client.volumes.get.call_args_list
        ]
        self.assertIn('fix151_vol_a', get_call_names)
        self.assertIn('fix151_vol_b', get_call_names)
        self.assertGreaterEqual(fake_volume.remove.call_count, 2)
        for call in fake_volume.remove.call_args_list:
            self.assertTrue(call.kwargs.get('force', False))

    def test_docker_sdk_unavailable_does_not_break_service_delete(self):
        with patch.dict('sys.modules', {'docker': None}):
            self.service.delete()
        self.assertFalse(Service.objects.filter(pk=self.service.pk).exists())

    def test_docker_get_failure_swallowed(self):
        fake_client = MagicMock()
        fake_client.volumes.get.side_effect = Exception("volume gone")

        fake_docker = MagicMock()
        fake_docker.from_env = MagicMock(return_value=fake_client)

        with patch.dict('sys.modules', {'docker': fake_docker}):
            self.service.delete()

        self.assertFalse(Service.objects.filter(pk=self.service.pk).exists())

    def test_signal_skipped_when_no_volumes(self):
        empty_service = Service.objects.create(name='fix151-empty', owner=self.user)
        fake_docker = MagicMock()
        fake_docker.from_env = MagicMock()
        with patch.dict('sys.modules', {'docker': fake_docker}):
            empty_service.delete()
        fake_docker.from_env.assert_not_called()
