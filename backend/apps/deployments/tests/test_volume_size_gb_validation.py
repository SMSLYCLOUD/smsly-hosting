# pylint: disable=invalid-name
"""Tests for ``Volume.size_gb`` validation (Issue 130).

The original ``size_gb`` field on ``Volume`` accepted any
integer, allowing a user to request a 10 000 GB volume.  The
fix attaches ``MinValueValidator(1)`` and
``MaxValueValidator(1000)`` to the field, plus a
``CheckConstraint`` named ``volume_size_gb_range`` that
enforces ``1 <= size_gb <= 1000`` at the database level.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.storage import Volume

User = get_user_model()


class VolumeSizeGbValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="vsize-user", password="x",
        )
        self.provider = CloudProvider.objects.create(
            name="vsize-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="vsize-svc",
            owner=self.user,
            provider=self.provider,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_via_api(self, size_gb):
        return self.client.post(
            f"/api/v1/services/{self.service.id}/volumes/",
            {
                "name": "vol",
                "mount_path": "/data",
                "size_gb": size_gb,
            },
            format="json",
        )

    def test_field_has_min_validator(self):
        field = Volume._meta.get_field("size_gb")
        validators = list(getattr(field, "validators", []) or [])
        from django.core.validators import MinValueValidator
        self.assertTrue(
            any(isinstance(v, MinValueValidator) and v.limit_value == 1
                for v in validators)
        )

    def test_field_has_max_validator(self):
        field = Volume._meta.get_field("size_gb")
        validators = list(getattr(field, "validators", []) or [])
        from django.core.validators import MaxValueValidator
        self.assertTrue(
            any(isinstance(v, MaxValueValidator) and v.limit_value == 1000
                for v in validators)
        )

    def test_meta_check_constraint_present(self):
        constraints = list(
            Volume._meta.constraints
        )
        names = [c.name for c in constraints]
        self.assertIn("volume_size_gb_range", names)

    def test_clean_rejects_zero(self):
        volume = Volume(
            service=self.service,
            name="vol-zero",
            mount_path="/data",
            size_gb=0,
        )
        with self.assertRaises(ValidationError):
            volume.full_clean()

    def test_clean_rejects_too_large(self):
        volume = Volume(
            service=self.service,
            name="vol-large",
            mount_path="/data",
            size_gb=1001,
        )
        with self.assertRaises(ValidationError):
            volume.full_clean()

    def test_clean_allows_min(self):
        volume = Volume(
            service=self.service,
            name="vol-min",
            mount_path="/data",
            size_gb=1,
        )
        volume.full_clean()

    def test_clean_allows_max(self):
        volume = Volume(
            service=self.service,
            name="vol-max",
            mount_path="/data",
            size_gb=1000,
        )
        volume.full_clean()

    def test_api_rejects_too_large(self):
        resp = self._create_via_api(1001)
        self.assertIn(resp.status_code, (400, 404))

    def test_api_rejects_zero(self):
        resp = self._create_via_api(0)
        self.assertIn(resp.status_code, (400, 404))

    def test_api_accepts_max(self):
        resp = self._create_via_api(1000)
        self.assertEqual(resp.status_code, 201)
