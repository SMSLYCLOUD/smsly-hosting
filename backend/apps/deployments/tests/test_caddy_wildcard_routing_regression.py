# pylint: disable=invalid-name
"""Regression test: wildcard domain routing must classify local services correctly.

BUG: _get_wildcard_known_hosts() used _resolve_effective_server() which checked
the latest deployment's target_server.  Services last deployed to a remote node
were misclassified as "remote" and excluded from @known_hosts.

FIX: Uses pre-fetched Subquery with target_is_local field instead.
"""

from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model

from apps.deployments.services.caddy_manager.config_generation import (
    _get_wildcard_known_hosts,
    _get_wildcard_remote_host_map,
)


User = get_user_model()


class TestWildcardRouting(TransactionTestCase):
    """Uses real DB with cleanup to avoid mock complexity."""

    def setUp(self):
        from apps.deployments.models.core import ManagedServer
        from apps.deployments.models import Service

        self.user = User.objects.create_superuser('testadmin', 'admin@test.com', 'pass')
        self.primary = ManagedServer.objects.create(
            name='primary-test', host='192.168.1.100', is_primary=True,
            status='ACTIVE', owner=self.user,
        )
        self.remote = ManagedServer.objects.create(
            name='remote-test', host='10.0.0.2', wg_address='10.100.0.2',
            is_primary=False, status='ACTIVE', owner=self.user,
        )

    def tearDown(self):
        from apps.deployments.models.core import ManagedServer
        from apps.deployments.models import Service, Deployment
        Deployment.objects.all().delete()
        Service.objects.all().delete()
        ManagedServer.objects.all().delete()
        self.user.delete()

    def test_local_service_no_deployment_is_known(self):
        from apps.deployments.models import Service
        Service.objects.create(
            name='local-svc', public_domain='local-svc.grid.smsly.cloud',
            server=self.primary, status='ACTIVE', owner=self.user,
        )
        hosts = _get_wildcard_known_hosts('grid.smsly.cloud')
        self.assertIn('local-svc.grid.smsly.cloud', hosts)

    def test_service_with_local_deployment_is_known(self):
        from apps.deployments.models import Service, Deployment
        svc = Service.objects.create(
            name='local-deployed', public_domain='ld.grid.smsly.cloud',
            server=self.primary, status='ACTIVE', owner=self.user,
        )
        Deployment.objects.create(
            service=svc, status='ACTIVE', target_is_local=True,
            commit_hash='abc123',
        )
        hosts = _get_wildcard_known_hosts('grid.smsly.cloud')
        self.assertIn('ld.grid.smsly.cloud', hosts)

    def test_service_deployed_to_remote_excluded(self):
        from apps.deployments.models import Service, Deployment
        svc = Service.objects.create(
            name='remote-deployed', public_domain='rd.grid.smsly.cloud',
            server=self.primary, status='ACTIVE', owner=self.user,
        )
        Deployment.objects.create(
            service=svc, status='ACTIVE',
            target_server=self.remote, target_is_local=False,
            commit_hash='abc123',
        )
        hosts = _get_wildcard_known_hosts('grid.smsly.cloud')
        self.assertNotIn('rd.grid.smsly.cloud', hosts)

    def test_remote_server_service_excluded(self):
        from apps.deployments.models import Service, Deployment
        svc = Service.objects.create(
            name='remote-svc', public_domain='rs.grid.smsly.cloud',
            server=self.remote, status='ACTIVE', owner=self.user,
        )
        Deployment.objects.create(
            service=svc, status='ACTIVE',
            target_server=self.remote, target_is_local=False,
            commit_hash='abc123',
        )
        hosts = _get_wildcard_known_hosts('grid.smsly.cloud')
        self.assertNotIn('rs.grid.smsly.cloud', hosts)

    def test_preview_excluded(self):
        from apps.deployments.models import Service
        Service.objects.create(
            name='preview-svc', public_domain='prev.grid.smsly.cloud',
            server=self.primary, status='ACTIVE', owner=self.user,
            is_preview=True,
        )
        hosts = _get_wildcard_known_hosts('grid.smsly.cloud')
        self.assertNotIn('prev.grid.smsly.cloud', hosts)

    def test_hidden_domain_excluded(self):
        from apps.deployments.models import Service
        Service.objects.create(
            name='hidden-svc', public_domain='hid.grid.smsly.cloud',
            server=self.primary, status='ACTIVE', owner=self.user,
            public_domain_hidden=True,
        )
        hosts = _get_wildcard_known_hosts('grid.smsly.cloud')
        self.assertNotIn('hid.grid.smsly.cloud', hosts)

    def test_empty_wildcard_returns_empty(self):
        self.assertEqual(_get_wildcard_known_hosts(''), [])

    def test_remote_service_in_remote_map(self):
        from apps.deployments.models import Service, Deployment
        svc = Service.objects.create(
            name='remote-map-svc', public_domain='rm.grid.smsly.cloud',
            server=self.remote, status='ACTIVE', owner=self.user,
        )
        Deployment.objects.create(
            service=svc, status='ACTIVE',
            target_server=self.remote, target_is_local=False,
            commit_hash='abc123',
        )
        remote_map = _get_wildcard_remote_host_map('grid.smsly.cloud')
        all_hosts = [h for hosts in remote_map.values() for h in hosts]
        self.assertIn('rm.grid.smsly.cloud', all_hosts)

    def test_known_and_remote_mutually_exclusive(self):
        from apps.deployments.models import Service, Deployment
        local_svc = Service.objects.create(
            name='local-mut', public_domain='lm.grid.smsly.cloud',
            server=self.primary, status='ACTIVE', owner=self.user,
        )
        remote_svc = Service.objects.create(
            name='remote-mut', public_domain='rm2.grid.smsly.cloud',
            server=self.remote, status='ACTIVE', owner=self.user,
        )
        Deployment.objects.create(
            service=local_svc, status='ACTIVE', target_is_local=True,
            commit_hash='abc123',
        )
        Deployment.objects.create(
            service=remote_svc, status='ACTIVE',
            target_server=self.remote, target_is_local=False,
            commit_hash='abc123',
        )
        known = set(_get_wildcard_known_hosts('grid.smsly.cloud'))
        remote_map = _get_wildcard_remote_host_map('grid.smsly.cloud')
        remote = set()
        for hosts in remote_map.values():
            remote.update(hosts)
        self.assertEqual(known & remote, set(), f"Overlap: {known & remote}")
