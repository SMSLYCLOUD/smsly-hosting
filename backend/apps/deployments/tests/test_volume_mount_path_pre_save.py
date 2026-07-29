from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.storage import Volume
from apps.deployments.signals import _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES

User = get_user_model()


class VolumeMountPathPreSaveSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="vmp-user", password="x",
        )
        self.provider = CloudProvider.objects.create(
            name="vmp-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="vmp-svc",
            owner=self.user,
            provider=self.provider,
        )

    def test_allowed_prefixes_constant_present(self):
        self.assertIn("/var/lib/smsly/volumes/", _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES)
        self.assertIn("/data/", _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES)
        self.assertIn("/opt/smsly/data/", _VOLUME_MOUNT_PATH_ALLOWED_PREFIXES)

    def test_save_rejects_etc_path(self):
        v = Volume(
            service=self.service,
            name="vol-etc",
            mount_path="/etc/passwd",
        )
        with self.assertRaises(ValidationError):
            v.save()

    def test_save_rejects_var_run_docker_sock(self):
        v = Volume(
            service=self.service,
            name="vol-sock",
            mount_path="/var/run/docker.sock",
        )
        with self.assertRaises(ValidationError):
            v.save()

    def test_save_rejects_proc_path(self):
        v = Volume(
            service=self.service,
            name="vol-proc",
            mount_path="/proc/cpuinfo",
        )
        with self.assertRaises(ValidationError):
            v.save()

    def test_save_rejects_root(self):
        v = Volume(
            service=self.service,
            name="vol-root",
            mount_path="/",
        )
        with self.assertRaises(ValidationError):
            v.save()

    def test_save_rejects_empty(self):
        v = Volume(
            service=self.service,
            name="vol-empty",
            mount_path="",
        )
        with self.assertRaises(ValidationError):
            v.save()

    def test_save_accepts_data_prefix(self):
        v = Volume(
            service=self.service,
            name="vol-data",
            mount_path="/data/uploads",
        )
        v.save()
        self.assertEqual(Volume.objects.filter(name="vol-data").count(), 1)

    def test_save_accepts_var_lib_smsly_volumes(self):
        v = Volume(
            service=self.service,
            name="vol-smsly",
            mount_path="/var/lib/smsly/volumes/abc",
        )
        v.save()
        self.assertEqual(Volume.objects.filter(name="vol-smsly").count(), 1)

    def test_save_accepts_opt_smsly_data(self):
        v = Volume(
            service=self.service,
            name="vol-optdata",
            mount_path="/opt/smsly/data/foo",
        )
        v.save()
        self.assertEqual(Volume.objects.filter(name="vol-optdata").count(), 1)
