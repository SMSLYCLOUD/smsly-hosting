# pylint: disable=invalid-name
"""Tests for addon maintenance service behavior."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.maintenance import AddonMaintenanceService
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


class AddonMaintenanceServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="addonmaint",
            email="addonmaint@test.com",
            password="testpass123",
        )
        self.service = Service.objects.create(name="api", owner=self.user)
        self.addon = Addon.objects.create(
            service=self.service,
            name="pg-main",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
            connection_url="postgres://user:pass@localhost:5432/appdb",
        )

    def test_rotate_credentials_fails_closed_when_not_implemented(self):
        maintenance = AddonMaintenanceService(self.addon)
        result = maintenance.rotate_credentials()
        self.assertEqual(result.get("status"), "not_implemented")
        self.assertIn("not implemented", result.get("error", "").lower())
