"""Addon migrate pre-flight checks (raise before touching docker)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.addon_migrate import migrate_addon_mode
from apps.deployments.models import Addon, Service

User = get_user_model()


class MigratePrecheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="migratepre", password="x")
        self.service = Service.objects.create(name="migsvc", owner=self.user)

    def _addon(self, **kwargs):
        defaults = dict(
            service=self.service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            connection_url="postgresql://u:pw@postgres-x:5432/db")
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def test_bad_target_rejected(self):
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(self._addon().id), "whatever")

    def test_non_postgres_rejected(self):
        addon = self._addon(addon_type="REDIS")
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(addon.id), "container")

    def test_inactive_rejected(self):
        addon = self._addon(status=Addon.Status.FAILED)
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(addon.id), "container")

    def test_same_mode_rejected(self):
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(self._addon().id), "shared")

    def test_missing_url_rejected(self):
        addon = self._addon(connection_url="")
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(addon.id), "container")
