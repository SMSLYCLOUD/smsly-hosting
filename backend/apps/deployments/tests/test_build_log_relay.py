"""Tests for build-log WS relay target resolution (node live tail)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.consumers.build_log import _resolve_buildlog_relay_target
from apps.deployments.models.core import Deployment, ManagedServer, Service

User = get_user_model()


class BuildLogRelayTargetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='relayuser', password='testpassword')
        self.server = ManagedServer.objects.create(
            name='remote-node', host='10.0.0.5', is_primary=False, owner=self.user,
        )
        self.service = Service.objects.create(name='relay-service', owner=self.user)
        self.deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.BUILDING,
            commit_hash="abcdef",
        )

    def test_local_deployment_no_relay(self):
        self.service.active_target_type = 'local'
        self.service.active_host_ip = '127.0.0.1'
        self.service.save()
        self.assertEqual(
            _resolve_buildlog_relay_target(str(self.deployment.id)), (None, None),
        )

    def test_remote_deployment_resolves(self):
        self.service.active_target_type = 'remote'
        self.service.active_host_ip = '10.0.0.5'
        self.service.save()
        self.deployment.remote_deployment_id = 'remotedep123'
        self.deployment.save(update_fields=['remote_deployment_id'])
        remote_id, server = _resolve_buildlog_relay_target(str(self.deployment.id))
        self.assertEqual(remote_id, 'remotedep123')
        self.assertIsNotNone(server)
        self.assertEqual(server.id, self.server.id)

    def test_remote_target_without_remote_id_no_relay(self):
        self.service.active_target_type = 'remote'
        self.service.active_host_ip = '10.0.0.5'
        self.service.save()
        self.assertEqual(
            _resolve_buildlog_relay_target(str(self.deployment.id)), (None, None),
        )

    def test_missing_deployment_no_relay(self):
        self.assertEqual(
            _resolve_buildlog_relay_target('00000000-0000-0000-0000-000000000000'),
            (None, None),
        )
