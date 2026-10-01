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
