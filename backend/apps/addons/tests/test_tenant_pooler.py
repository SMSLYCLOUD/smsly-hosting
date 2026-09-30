"""Tenant pooler: render/push/status without docker (mocked subprocess)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services import tenant_pooler as tp
from apps.deployments.models import Addon, Service

User = get_user_model()


def _render(pools):
    ini, userlist = tp.render_tenants_config(pools)
    return ini, userlist


class RenderTests(TestCase):
    def test_render_one_pool_per_alias(self):
        ini, userlist = _render([{
            'alias': 'postgres-acme', 'user': 'u1', 'db': 'd1', 'password': 'pw1',
        }])
        self.assertIn('postgres-acme = host=smsly-shared-postgres port=5432 dbname=d1', ini)
        # PgBouncer routes by database name: the dbname key keeps the
        # provisioned URL form (alias host + real dbname) working.
        self.assertIn('d1 = host=smsly-shared-postgres port=5432 dbname=d1', ini)
        self.assertIn('pool_mode = transaction', ini)
        self.assertIn('"u1" "pw1"', userlist)

    def test_render_rejects_duplicate_db_keys(self):
        with self.assertRaises(RuntimeError):
            _render([
                {'alias': 'a1', 'user': 'u1', 'db': 'd', 'password': 'p1'},
                {'alias': 'a2', 'user': 'u2', 'db': 'd', 'password': 'p2'},
            ])

    def test_render_caps_server_connections_per_pool(self):
        ini, _ = _render([{
            'alias': 'postgres-acme', 'user': 'u1', 'db': 'd1', 'password': 'pw1',
        }])
        self.assertIn('max_db_connections=10', ini)

    def test_render_server_override_and_loopback_rejected(self):
        ini, _ = _render([{
            'alias': 'a', 'user': 'u', 'db': 'd', 'password': 'p',
            'server': 'smsly-shared-postgres-2',
        }])
        self.assertIn('host=smsly-shared-postgres-2 ', ini)
        with self.assertRaises(RuntimeError):
            _render([{
                'alias': 'a', 'user': 'u', 'db': 'd', 'password': 'p',
                'server': '127.0.0.1',
            }])

    def test_render_has_pooler_required_keys(self):
        """PgBouncer must see listen/auth/pool keys or it exits on startup."""
        ini, _ = _render([])
        for key in ('listen_addr = 0.0.0.0', 'listen_port = 5432',
                    'auth_type = scram-sha-256',
                    'pool_mode = transaction'):
            self.assertIn(key, ini)
        self.assertIn('[databases]', ini)
        self.assertIn('[pgbouncer]', ini)

    def test_render_needs_no_admin_password(self):
        """PgBouncer has no admin-account requirement (pgcat did)."""
        ini, userlist = _render([])
        tp.validate_rendered_config(ini, userlist)  # must not raise

    def test_render_rejects_unsafe_tokens(self):
        with self.assertRaises(RuntimeError):
            _render([{'alias': 'a/b', 'user': 'u', 'db': 'd', 'password': 'p'}])
        with self.assertRaises(RuntimeError):
            _render([{'alias': 'a', 'user': 'u', 'db': 'd', 'password': 'has "quote'}])

    def test_render_self_validates_required_schema(self):
        """render_tenants_config must fail closed (not push a bad config)."""
        with self.assertRaises(RuntimeError):
            tp.validate_rendered_config('[databases]\n')
        ini, userlist = _render([])
        tp.validate_rendered_config(ini, userlist)  # must not raise


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

    def _push(self, remote_ini, remote_ul):
        with mock.patch.object(tp, 'tenants_container_name', return_value='c1'), \
             mock.patch.object(tp, 'container_running', return_value=True), \
             mock.patch.object(tp, '_read_remote_file',
                               side_effect=[remote_ini, remote_ul]), \
             mock.patch.object(tp, '_write_remote_file', return_value={}) as writer, \
             mock.patch.object(tp, '_run', return_value={}) as runner:
            result = tp.push_tenants_config()
        return result, writer, runner

    def test_no_restart_when_unchanged(self):
        ini, userlist = _render(tp.list_tenant_pools(with_passwords=True))
        result, writer, runner = self._push(ini, userlist)
        self.assertTrue(result['ok'])
        self.assertFalse(result['changed'])
        self.assertFalse(result['restarted'])
        writer.assert_not_called()
        restart_calls = [c for c in runner.call_args_list if 'restart' in str(c)]
        self.assertEqual(restart_calls, [])

    def test_write_and_reload_when_changed(self):
        # Online HUP reload: config changes apply without dropping
        # pooled connections (no container restart).
        result, writer, runner = self._push('stale ini', 'stale userlist')
        self.assertTrue(result['ok'])
        self.assertTrue(result['changed'])
        self.assertFalse(result['restarted'])
        self.assertEqual(writer.call_count, 2)
        hup_calls = [c for c in runner.call_args_list if 'HUP' in str(c)]
        self.assertEqual(len(hup_calls), 1)

    def test_restart_when_hup_fails(self):
        with mock.patch.object(tp, 'tenants_container_name', return_value='c1'), \
             mock.patch.object(tp, 'container_running', return_value=True), \
             mock.patch.object(tp, '_read_remote_file', return_value='stale'), \
             mock.patch.object(tp, '_write_remote_file', return_value={}), \
             mock.patch.object(tp, '_run',
                               side_effect=[{'error': 'no kill'}, {}, {}]) as runner:
            result = tp.push_tenants_config()
        self.assertTrue(result['ok'])
        self.assertTrue(result['changed'])
        self.assertTrue(result['restarted'])
        restart_calls = [c for c in runner.call_args_list if 'restart' in str(c)]
        self.assertEqual(len(restart_calls), 1)

    def test_missing_container(self):
        with mock.patch.object(tp, 'tenants_container_name', return_value=None):
            result = tp.push_tenants_config()
        self.assertFalse(result['ok'])
        self.assertIn('not found', result['error'])

    def test_render_failure_leaves_pooler_untouched(self):
        """A bad render must never push a crash-looping config."""
        with mock.patch.object(tp, 'tenants_container_name', return_value='c1'), \
             mock.patch.object(tp, '_write_remote_file') as writer, \
             mock.patch.object(tp, '_run', return_value={}) as runner, \
             mock.patch.object(tp, 'render_tenants_config',
                               side_effect=RuntimeError('bad render')):
            result = tp.push_tenants_config()
        self.assertFalse(result['ok'])
        writer.assert_not_called()
        restart_calls = [c for c in runner.call_args_list if 'restart' in str(c)]
        self.assertEqual(restart_calls, [])


class ReconcileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tenantrecon", password="x")
        self.service = Service.objects.create(name="treconsvc", owner=self.user)
        Addon.objects.create(
            service=self.service, name="pg-shared", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            connection_url="postgresql://u1:pw1@postgres-keep:5432/d1")
        Addon.objects.create(
            service=self.service, name="pg-ded", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://u2:pw2@postgres-stale:5432/d2")

    def _recon(self, networks, aliases_by_net):
        calls = []

        def fake_sp_run(cmd, timeout=60):
            calls.append(cmd)
            m = mock.Mock()
            m.returncode = 0
            m.stderr = b''
            return m

        with mock.patch.object(tp, 'tenants_container_name', return_value='c1'), \
             mock.patch.object(tp, '_pooler_networks', return_value=networks), \
             mock.patch('apps.addons.services.shared_postgres._endpoint_aliases',
                        side_effect=lambda c, n: list(aliases_by_net.get(n, []))), \
             mock.patch('apps.addons.services.shared_postgres._run',
                        side_effect=fake_sp_run):
            result = tp.reconcile_pooler_aliases()
        return result, calls

    def test_strips_dedicated_alias_keeps_shared(self):
        result, calls = self._recon(
            ['smsly-net'],
            {'smsly-net': ['c1', 'pgbouncer-tenants', 'postgres-keep',
                           'postgres-stale']})
        self.assertTrue(result['ok'])
        self.assertEqual(result['stripped'], ['postgres-stale@smsly-net'])
        reconnects = [c for c in calls if 'connect' in c]
        self.assertEqual(len(reconnects), 1)
        flat = ' '.join(reconnects[0])
        self.assertIn('postgres-keep', flat)
        self.assertNotIn('postgres-stale', flat)

    def test_detaches_network_with_only_stale_aliases(self):
        result, calls = self._recon(
            ['scoped-net'],
            {'scoped-net': ['postgres-stale']})
        self.assertTrue(result['ok'])
        self.assertEqual(result['stripped'], ['postgres-stale@scoped-net'])
        reconnects = [c for c in calls if 'connect' in c]
        self.assertEqual(reconnects, [])

    def test_clean_pooler_is_noop(self):
        result, calls = self._recon(
            ['smsly-net'],
            {'smsly-net': ['c1', 'pgbouncer-tenants', 'postgres-keep']})
        self.assertTrue(result['ok'])
        self.assertEqual(result['stripped'], [])
        self.assertEqual(calls, [])

    def test_push_runs_reconcile_even_when_unchanged(self):
        ini, userlist = _render(tp.list_tenant_pools(with_passwords=True))
        with mock.patch.object(tp, 'tenants_container_name', return_value='c1'), \
             mock.patch.object(tp, 'container_running', return_value=True), \
             mock.patch.object(tp, '_read_remote_file',
                               side_effect=[ini, userlist]), \
             mock.patch.object(tp, '_run', return_value={}), \
             mock.patch.object(tp, 'reconcile_pooler_aliases',
                               return_value={'ok': True, 'stripped': []}) as recon:
            result = tp.push_tenants_config()
        self.assertTrue(result['ok'])
        self.assertFalse(result['changed'])
        recon.assert_called_once_with()
