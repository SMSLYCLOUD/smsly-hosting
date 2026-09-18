"""Tenant pooler: render/push/status without docker (mocked subprocess)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services import tenant_pooler as tp
from apps.deployments.models import Addon, Service

User = get_user_model()


class RenderTests(TestCase):
    def test_render_one_pool_per_alias(self):
        content = tp.render_tenants_config([{
            'alias': 'postgres-acme', 'user': 'u1', 'db': 'd1', 'password': 'pw1',
        }])
        self.assertIn('[pools.postgres-acme]', content)
        self.assertIn('pool_mode = "transaction"', content)
        self.assertIn('database = "d1"', content)
        self.assertIn('password = "pw1"', content)
        self.assertIn('smsly-shared-postgres', content)


class ListPoolsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tenantpool", password="x")
        self.service = Service.objects.create(name="tpoolsvc", owner=self.user)

    def test_lists_only_active_shared_postgres(self):
        Addon.objects.create(
            service=self.service, name="pg1", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            connection_url="postgresql://u1:pw1@postgres-a:5432/d1")
        Addon.objects.create(
            service=self.service, name="pg2", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://u2:pw2@somehost:5432/d2")
        Addon.objects.create(
            service=self.service, name="pg3", addon_type="POSTGRES",
            status=Addon.Status.FAILED, provision_mode="shared",
            connection_url="postgresql://u3:pw3@postgres-c:5432/d3")
        pools = tp.list_tenant_pools()
        self.assertEqual([p['alias'] for p in pools], ['postgres-a'])
        self.assertNotIn('password', pools[0])

    def test_passwords_only_on_request(self):
        Addon.objects.create(
            service=self.service, name="pg1", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            connection_url="postgresql://u1:pw1@postgres-a:5432/d1")
        pools = tp.list_tenant_pools(with_passwords=True)
        self.assertEqual(pools[0]['password'], 'pw1')


class PushTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tenantpush", password="x")
        self.service = Service.objects.create(name="tpushsvc", owner=self.user)
        Addon.objects.create(
            service=self.service, name="pg1", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            connection_url="postgresql://u1:pw1@postgres-a:5432/d1")

    def _push(self, remote_content):
        with mock.patch.object(tp, 'tenants_container_name', return_value='c1'), \
             mock.patch.object(tp, 'container_running', return_value=True), \
             mock.patch.object(tp, '_read_remote_toml', return_value=remote_content), \
             mock.patch.object(tp, '_write_remote_toml', return_value={}) as writer, \
             mock.patch.object(tp, '_run', return_value={}) as runner:
            result = tp.push_tenants_config()
        return result, writer, runner

    def test_no_restart_when_unchanged(self):
        content = tp.render_tenants_config(tp.list_tenant_pools(with_passwords=True))
        result, writer, runner = self._push(content)
        self.assertTrue(result['ok'])
        self.assertFalse(result['changed'])
        self.assertFalse(result['restarted'])
        writer.assert_not_called()
        restart_calls = [c for c in runner.call_args_list if 'restart' in str(c)]
        self.assertEqual(restart_calls, [])

    def test_write_and_restart_when_changed(self):
        result, writer, runner = self._push('stale content')
        self.assertTrue(result['ok'])
        self.assertTrue(result['changed'])
        self.assertTrue(result['restarted'])
        writer.assert_called_once()

    def test_missing_container(self):
        with mock.patch.object(tp, 'tenants_container_name', return_value=None):
            result = tp.push_tenants_config()
        self.assertFalse(result['ok'])
        self.assertIn('not found', result['error'])
