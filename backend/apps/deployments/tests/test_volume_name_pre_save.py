# pylint: disable=invalid-name
"""Tests for the Volume.name ``pre_save`` signal (Issue 140).

The serializer runs ``_validate_volume_name`` first, but admin
scripts or direct ORM writes can bypass it.  The model-level
``clean()`` is not invoked automatically by ``save()``, so a
``pre_save`` signal enforces the
``^[a-zA-Z0-9_-]{1,64}$`` regex on every save.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.storage import Volume

User = get_user_model()


class VolumeNamePreSaveSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="vname-user", password="x",
        )
        self.provider = CloudProvider.objects.create(
            name="vname-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="vname-svc",
            owner=self.user,
            provider=self.provider,
        )

    def test_regex_constant_matches_finding(self):
        regex = Volume._VOLUME_NAME_RE.pattern
        self.assertEqual(regex, r"^[a-zA-Z0-9_-]{1,64}$")

    def test_save_rejects_empty_name(self):
        v = Volume(service=self.service, name="", mount_path="/data")
        with self.assertRaises(ValidationError):
            v.save()

    def test_save_rejects_too_long_name(self):
        v = Volume(
            service=self.service, name="a" * 65, mount_path="/data",
        )
        with self.assertRaises(ValidationError):
            v.save()

    def test_save_rejects_dot_in_name(self):
        v = Volume(service=self.service, name="bad.name", mount_path="/data")
        with self.assertRaises(ValidationError):
            v.save()

    def test_save_rejects_special_chars(self):
        for bad in ("vol!name", "vol name", "vol/name", "vol*name"):
            v = Volume(service=self.service, name=bad, mount_path="/data")
            with self.assertRaises(ValidationError):
                v.save()

    def test_save_accepts_simple_name(self):
        v = Volume(service=self.service, name="data-1", mount_path="/data")
        v.save()
        self.assertEqual(Volume.objects.filter(name="data-1").count(), 1)

    def test_save_accepts_underscore_and_caps(self):
        v = Volume(
            service=self.service, name="Data_Vol_1", mount_path="/data",
        )
        v.save()
        self.assertEqual(Volume.objects.filter(name="Data_Vol_1").count(), 1)

    def test_save_accepts_max_length(self):
        name = "a" * 64
        v = Volume(service=self.service, name=name, mount_path="/data")
        v.save()
        self.assertEqual(Volume.objects.filter(name=name).count(), 1)
