"""Dedicated PgBouncer addon: render, target resolution, dispatch (no docker)."""
import base64
import hashlib
import hmac as _hmac
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.addon_provisioner import (
    AddonProvisioner,
    addon_provisioner,
)
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


def _scram_check(password: str, verifier: str) -> bool:
    """Independently verify an RFC 5802 SCRAM-SHA-256 verifier string."""
    try:
        scheme, rest = verifier.split('$', 1)
        if scheme != 'SCRAM-SHA-256':
            return False
        parts = rest.split(':')
        iters, salt_b64, stored_b64, server_b64 = parts
        salt = base64.b64decode(salt_b64)
        salted = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, int(iters))
        client_key = _hmac.new(salted, b'Client Key', hashlib.sha256).digest()
        server_key = _hmac.new(salted, b'Server Key', hashlib.sha256).digest()
        import hashlib as _h
        return (_h.sha256(client_key).digest() == base64.b64decode(stored_b64)
                and server_key == base64.b64decode(server_b64))
    except Exception:
        return False


class ScramVerifierTests(TestCase):
    def test_format_and_roundtrip(self):
        v = AddonProvisioner._scram_verifier('s3cret!')
        self.assertTrue(v.startswith('SCRAM-SHA-256$4096:'))
        self.assertTrue(_scram_check('s3cret!', v))

    def test_salts_differ(self):
        self.assertNotEqual(
            AddonProvisioner._scram_verifier('same'),
            AddonProvisioner._scram_verifier('same'))

    def test_wrong_password_rejected(self):
        v = AddonProvisioner._scram_verifier('right')
        self.assertFalse(_scram_check('wrong', v))


class RenderTests(TestCase):
    def test_ini_and_userlist(self):
        ini, userlist = AddonProvisioner._render_pgbouncer_config(
            'appuser', 'pg-host', 5432, 'appdb',
            AddonProvisioner._scram_verifier('pw'))
        self.assertIn('host=pg-host port=5432 dbname=appdb', ini)
        self.assertIn('listen_port = 6432', ini)
        self.assertIn('pool_mode = transaction', ini)
        self.assertIn('auth_type = scram-sha-256', ini)
        self.assertTrue(userlist.startswith('"appuser" "SCRAM-SHA-256$'))

    def test_no_plaintext_password_leak(self):
        ini, userlist = AddonProvisioner._render_pgbouncer_config(
            'u', 'h', 5432, 'd', AddonProvisioner._scram_verifier('super-secret-pw'))
        self.assertNotIn('super-secret-pw', ini + userlist)


class TargetResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pgbsvc", password="x")
        self.service = Service.objects.create(name="pgbsvc", owner=self.user)

    def _addon(self, **kw):
        base = dict(service=self.service, name='pooler', addon_type='PGBOUNCER',
                    status=Addon.Status.ACTIVE)
        base.update(kw)
        return Addon.objects.create(**base)

    def test_refuses_without_postgres(self):
        addon = self._addon()
        with self.assertRaisesMessage(ValueError, 'PostgreSQL'):
            addon_provisioner._pgbouncer_target(addon)

    def test_resolves_direct_url(self):
        Addon.objects.create(
            service=self.service, name='db', addon_type='POSTGRES',
            status=Addon.Status.ACTIVE,
            connection_url='postgresql://bob:secret1@pg-alias:5432/appdb')
        pooler = self._addon()
        host, port, user, db, pw = addon_provisioner._pgbouncer_target(pooler)
        self.assertEqual((host, port, user, db, pw),
                         ('pg-alias', 5432, 'bob', 'appdb', 'secret1'))

    def test_shared_target_resolves_to_server_not_pooler(self):
        Addon.objects.create(
            service=self.service, name='db', addon_type='POSTGRES',
            status=Addon.Status.ACTIVE, provision_mode='shared',
            pooler_routed=True,
            connection_url='postgresql://u:p@some-pooler-alias:5432/shdb')
        pooler = self._addon()
        host, _port, _u, _d, _p = addon_provisioner._pgbouncer_target(pooler)
        from apps.addons.services.shared_postgres import SHARED_CONTAINER
        self.assertEqual(host, SHARED_CONTAINER)


class DispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pgbdisp", password="x")
        self.service = Service.objects.create(name="pgbdisp", owner=self.user)

    def _addon(self, **kw):
        base = dict(service=self.service, name='pooler', addon_type='PGBOUNCER',
                    status=Addon.Status.ACTIVE)
        base.update(kw)
        return Addon.objects.create(**base)

    def test_provision_routes_to_pgbouncer(self):
        addon = self._addon()
        prov = AddonProvisioner()
        with mock.patch.object(
                AddonProvisioner, '_provision_pgbouncer',
                return_value=('cid123', 'postgresql://u:p@h:6432/d')) as m, \
             mock.patch.object(AddonProvisioner, '_ensure_network', return_value=None), \
             mock.patch.object(AddonProvisioner, '_connect_addon_networks', return_value=None):
            cid, url = prov.provision(addon)
        self.assertEqual((cid, url), ('cid123', 'postgresql://u:p@h:6432/d'))
        m.assert_called_once()

    def test_recreate_routes_to_pgbouncer(self):
        addon = self._addon(
            connection_url='postgresql://u:p@h:6432/d')
        prov = AddonProvisioner()
        with mock.patch.object(
                AddonProvisioner, '_provision_pgbouncer',
                return_value=('cid9', 'postgresql://u:p@h:6432/d')) as m, \
             mock.patch.object(AddonProvisioner, '_ensure_network', return_value=None), \
             mock.patch.object(AddonProvisioner, '_connect_addon_networks', return_value=None):
            cid, url = prov.provision(addon)
        # Recreate path keeps the persisted URL but still rebuilds via pgbouncer
        self.assertEqual(url, 'postgresql://u:p@h:6432/d')
        m.assert_called_once()
