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

    def test_rotate_credentials_returns_not_implemented_for_unsupported_type(self):
        self.addon.addon_type = Addon.Type.MYSQL
        self.addon.save(update_fields=["addon_type"])
        maintenance = AddonMaintenanceService(self.addon)
        result = maintenance.rotate_credentials()
        self.assertEqual(result.get("status"), "not_implemented")
        self.assertIn("not implemented", result.get("error", "").lower())

    def test_rotate_credentials_postgres_fails_closed_on_connection_error(self):
        from unittest.mock import patch

        maintenance = AddonMaintenanceService(self.addon)
        with patch.object(
            maintenance.proxy,
            "get_connection",
            side_effect=OSError("connection refused"),
        ):
            result = maintenance.rotate_credentials()
        self.assertEqual(result.get("status"), "failed")
        self.assertIn("connection refused", result.get("error", "").lower())
