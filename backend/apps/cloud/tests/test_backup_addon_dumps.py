"""Service backups must include addon databases (mocked docker)."""
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.cloud.services.backup_service import operations as ops
from apps.deployments.models import Addon, Service

User = get_user_model()


def _mk_container(env=None, exec_results=None, archive_bits=None):
    ctr = mock.Mock()
    ctr.attrs = {'Config': {'Env': env or []}}
    results = list(exec_results or [])

    def _exec(cmd, environment=None, timeout=None):
        if results:
            return results.pop(0)
        m = mock.Mock()
        m.exit_code = 0
        m.output = b'DUMP'
        return m

    ctr.exec_run.side_effect = _exec
    if archive_bits is not None:
        ctr.get_archive.return_value = (archive_bits, {})
    return ctr


def _ok(output=b'DUMP'):
    m = mock.Mock()
    m.exit_code = 0
    m.output = output
    return m


def _fail(output=b'boom'):
    m = mock.Mock()
    m.exit_code = 1
    m.output = output
    return m


class AddonDumpTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bkpuser", password="x")
        self.service = Service.objects.create(name="bkpsvc", owner=self.user)
        self.tmp = tempfile.mkdtemp()

    def _addon(self, name, atype, mode='container', url=''):
        return Addon.objects.create(
            service=self.service, name=name, addon_type=atype,
            status=Addon.Status.ACTIVE, provision_mode=mode,
            connection_url=url)

    def _client(self, ctr):
        client = mock.Mock()
        client.containers.get.side_effect = lambda n: ctr
        return client

    def test_container_postgres_dumped_with_manifest(self):
        self._addon('pg-main', 'POSTGRES',
                    url='postgresql://u1:pw1@pg-main:5432/d1')
        ctr = _mk_container(
            env=['POSTGRES_USER=u1', 'POSTGRES_DB=d1', 'POSTGRES_PASSWORD=pw1'])
        manifest = ops._dump_service_addons(
            self.service, self.tmp, docker_client=self._client(ctr))
        # container name resolves via canonical pattern; accept any key
        self.assertEqual(len(manifest), 1)
        entry = manifest[0]
        self.assertEqual(entry['addon'], 'pg-main')
        self.assertEqual(entry['filename'], 'addon_pg-main_dump.sql')
        with open(os.path.join(self.tmp, entry['filename']), 'rb') as f:
            self.assertEqual(f.read(), b'DUMP')

    def test_pg_dump_fallback_to_pg_dumpall(self):
        self._addon('pg-fb', 'POSTGRES',
                    url='postgresql://u1:pw1@pg-fb:5432/d1')
        ctr = _mk_container(
            env=['POSTGRES_USER=u1', 'POSTGRES_DB=d1', 'POSTGRES_PASSWORD=pw1'],
            exec_results=[_fail(), _ok(b'ALL')])
        manifest = ops._dump_service_addons(
            self.service, self.tmp, docker_client=self._client(ctr))
        with open(os.path.join(self.tmp, manifest[0]['filename']), 'rb') as f:
            self.assertEqual(f.read(), b'ALL')

    def test_failed_dump_raises_no_silent_tarball(self):
        self._addon('pg-bad', 'POSTGRES',
                    url='postgresql://u1:pw1@pg-bad:5432/d1')
        ctr = _mk_container(
            env=['POSTGRES_USER=u1', 'POSTGRES_DB=d1', 'POSTGRES_PASSWORD=pw1'],
            exec_results=[_fail(), _fail()])
        with self.assertRaises(RuntimeError):
            ops._dump_service_addons(
                self.service, self.tmp, docker_client=self._client(ctr))

    def test_stateless_service_returns_empty(self):
        self._addon('files', 'MINIO')
        manifest = ops._dump_service_addons(
            self.service, self.tmp, docker_client=self._client({}))
        self.assertEqual(manifest, [])

    def test_filename_slug(self):
        self.assertEqual(ops._addon_dump_filename('Postgres Foo_1!', 'POSTGRES'),
                         'addon_postgres-foo-1_dump.sql')
        self.assertEqual(ops._addon_dump_filename('r1', 'REDIS'),
                         'addon_r1_dump.rdb')


class AddonVolumeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bkpvol", password="x")
        self.service = Service.objects.create(name="bkpvolsvc", owner=self.user)
        self.tmp = tempfile.mkdtemp()

    def test_volumes_tarred_with_manifest(self):
        Addon.objects.create(
            service=self.service, name="pg-v", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://u:p@pg-v:5432/d")
        helper = mock.Mock()
        helper.logs.return_value = [b'CHUNK']
        ctr = mock.Mock()
        ctr.attrs = {'Mounts': [{'Type': 'volume', 'Name': 'pg-v-data',
                                 'Destination': '/var/lib/postgresql/data'},
                                {'Type': 'bind', 'Source': '/x',
                                 'Destination': '/y'}]}
        client = mock.Mock()
        client.containers.get.side_effect = lambda n: ctr
        client.containers.run.return_value = helper
        manifest, skipped = ops._backup_addon_volumes(
            self.service, self.tmp, docker_client=client)
        self.assertEqual(skipped, [])
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]['volume'], 'pg-v-data')
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, manifest[0]['filename'])))

    def test_missing_container_recorded_not_raised(self):
        Addon.objects.create(
            service=self.service, name="pg-gone", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://u:p@pg-gone:5432/d")
        import docker as _docker_mod
        client = mock.Mock()
        client.containers.get.side_effect = _docker_mod.errors.NotFound('nope')
        manifest, skipped = ops._backup_addon_volumes(
            self.service, self.tmp, docker_client=client)
        self.assertEqual(manifest, [])
        self.assertEqual(skipped, ['pg-gone'])


class RestoreAddonDumpTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bkprs", password="x")
        self.service = Service.objects.create(name="bkprssvc", owner=self.user)
        self.tmp = tempfile.mkdtemp()
        Addon.objects.create(
            service=self.service, name="pg-r", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://u1:pw1@pg-r:5432/d1")

    def _entry(self):
        path = os.path.join(self.tmp, 'addon_pg-r_dump.sql')
        with open(path, 'w') as f:
            f.write('SELECT 1;')
        return {'addon': 'pg-r', 'filename': 'addon_pg-r_dump.sql'}

    def test_missing_file_raises(self):
        client = mock.Mock()
        with self.assertRaises(RuntimeError):
            ops._restore_addon_dump(
                client, self.service,
                {'addon': 'pg-r', 'filename': 'nope.sql'}, self.tmp)

    def test_missing_addon_raises(self):
        client = mock.Mock()
        with self.assertRaises(RuntimeError):
            ops._restore_addon_dump(
                client, self.service,
                {'addon': 'pg-nope', 'filename': 'x.sql'}, self.tmp)

    def test_sql_restored_into_addon(self):
        entry = self._entry()
        res = mock.Mock()
        res.exit_code = 0
        res.output = b''
        ctr = mock.Mock()
        ctr.id = 'cid1'
        ctr.exec_run.return_value = res
        client = mock.Mock()
        client.containers.get.side_effect = lambda n: ctr
        with mock.patch('apps.cloud.services.backup_service.helpers._copy_file_to_container') as cfc:
            ops._restore_addon_dump(client, self.service, entry, self.tmp)
        cfc.assert_called_once()
        cmd = ctr.exec_run.call_args[0][0]
        self.assertEqual(cmd[:3], ['psql', '-U', 'u1'])
        self.assertIn('d1', cmd)


class SharedServerDumpTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _shared_client(self, dblist, dumps=None):
        ctr = mock.Mock()

        def _exec(cmd, environment=None, timeout=None):
            m = mock.Mock()
            if '-c' in cmd:
                m.exit_code = 0
                m.output = dblist
                return m
            m.exit_code = 0
            m.output = (dumps or {}).get(cmd[cmd.index('-d') + 1], b'SQL')
            return m

        ctr.exec_run.side_effect = _exec
        client = mock.Mock()
        client.containers.get.side_effect = lambda n: ctr
        return client, ctr

    def test_dumps_non_addon_dbs_only(self):
        client, ctr = self._shared_client(
            b'postgres\npolicy_db\naddon_db\n')
        with mock.patch.dict('os.environ',
                             {'SHARED_POSTGRES_PASSWORD': 'pw'}):
            manifest = ops._dump_shared_server(
                self.tmp, docker_client=client,
                exclude_dbs={'addon_db'})
        self.assertEqual([e['db'] for e in manifest],
                         ['postgres', 'policy_db'])
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, 'shared_db_policy_db.sql')))

    def test_missing_password_raises(self):
        client, _ = self._shared_client(b'postgres\n')
        with mock.patch.dict('os.environ', {'SHARED_POSTGRES_PASSWORD': ''}):
            with self.assertRaises(RuntimeError):
                ops._dump_shared_server(self.tmp, docker_client=client)

    def test_shared_restore_loads_through_postgres_db(self):
        path = os.path.join(self.tmp, 'shared_db_policy_db.sql')
        with open(path, 'w') as f:
            f.write('SELECT 1;')
        res = mock.Mock()
        res.exit_code = 0
        res.output = b''
        ctr = mock.Mock()
        ctr.id = 'cid9'
        ctr.exec_run.return_value = res
        client = mock.Mock()
        client.containers.get.side_effect = lambda n: ctr
        with mock.patch('apps.cloud.services.backup_service.helpers._copy_file_to_container'):
            ops._restore_shared_db(
                client, {'db': 'policy_db',
                         'filename': 'shared_db_policy_db.sql'},
                self.tmp, 'pw')
        cmd = ctr.exec_run.call_args[0][0]
        self.assertEqual(cmd[:6],
                         ['psql', '-h', '127.0.0.1', '-U', 'postgres', '-d'])
        self.assertIn('postgres', cmd)
