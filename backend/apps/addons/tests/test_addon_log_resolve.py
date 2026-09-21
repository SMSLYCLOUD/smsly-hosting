"""resolve_log_container: shared addons map to the shared server/pooler."""
from unittest import mock

from django.test import SimpleTestCase

from apps.addons.services.addon_provisioner import addon_provisioner


def _addon(**kwargs):
    addon = mock.MagicMock()
    addon.id = "abc-123"
    addon.addon_type = "POSTGRES"
    addon.provision_mode = "shared"
    addon.pooler_routed = False
    for k, v in kwargs.items():
        setattr(addon, k, v)
    return addon


class ResolveLogContainerTests(SimpleTestCase):
    def test_dedicated_uses_canonical_name_no_notice(self):
        name, notice = addon_provisioner.resolve_log_container(
            _addon(provision_mode="container"))
        self.assertEqual(name, "smsly-addon-postgres-abc-123")
        self.assertEqual(notice, "")

    def test_non_postgres_uses_canonical_name(self):
        name, notice = addon_provisioner.resolve_log_container(
            _addon(addon_type="REDIS", provision_mode="container"))
        self.assertEqual(name, "smsly-addon-redis-abc-123")
        self.assertEqual(notice, "")

    def test_shared_resolves_to_shared_server_with_notice(self):
        name, notice = addon_provisioner.resolve_log_container(_addon())
        self.assertEqual(name, "smsly-shared-postgres")
        self.assertTrue(notice)

    def test_pooler_routed_resolves_to_pooler(self):
        with mock.patch(
            "apps.addons.services.tenant_pooler.tenants_container_name",
            return_value="pooler-9",
        ):
            name, notice = addon_provisioner.resolve_log_container(
                _addon(pooler_routed=True))
        self.assertEqual(name, "pooler-9")
        self.assertTrue(notice)

    def test_pooler_routed_falls_back_to_shared(self):
        with mock.patch(
            "apps.addons.services.tenant_pooler.tenants_container_name",
            return_value=None,
        ):
            name, notice = addon_provisioner.resolve_log_container(
                _addon(pooler_routed=True))
        self.assertEqual(name, "smsly-shared-postgres")
        self.assertTrue(notice)
