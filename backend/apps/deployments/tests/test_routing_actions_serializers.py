from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.core import (
    Deployment,
    ManagedServer,
    Service,
)
from apps.deployments.utils.target import resolve_active_execution_target

User = get_user_model()

class TargetResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpassword')
        self.service = Service.objects.create(name='test-service', owner=self.user)
        self.server = ManagedServer.objects.create(
            name='remote-node', host='10.0.0.5', is_primary=False, owner=self.user
        )

    def test_resolve_local_target(self):
        self.service.active_target_type = 'local'
        self.service.active_host_ip = '127.0.0.1'
        self.service.active_runtime_id = 'local-container-id'
        self.service.save()

        target = resolve_active_execution_target(self.service)
        self.assertEqual(target['target_type'], 'local')
        self.assertEqual(target['host_ip'], '127.0.0.1')
        self.assertIsNone(target['server_obj'])

    def test_resolve_remote_target(self):
        self.service.active_target_type = 'remote'
        self.service.active_host_ip = '10.0.0.5'
        self.service.active_runtime_id = 'remote-container-id'
        self.service.save()

        target = resolve_active_execution_target(self.service)
        self.assertEqual(target['target_type'], 'remote')
        self.assertEqual(target['host_ip'], '10.0.0.5')
        self.assertEqual(target['server_obj'].id, self.server.id)

    def test_resolve_target_missing_metadata(self):
        with self.assertRaises(ValueError):
            resolve_active_execution_target(self.service)

class ActionRoutingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpassword')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.server = ManagedServer.objects.create(
            name='remote-node', host='10.0.0.5', is_primary=False, owner=self.user
        )
        self.service = Service.objects.create(
            name='test-service',
            owner=self.user,
            active_target_type='remote',
            active_host_ip='10.0.0.5',
            active_runtime_id='remote-container-id'
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            remote_deployment_id="remotedep123",
            commit_hash="abcdef"
        )

    @patch('apps.deployments.services.remote_orchestrator.RemoteOrchestrator._request')
    def test_logs_routed_to_active_host(self, mock_request):
        self.client.get(f"/api/v1/deployments/{self.deployment.id}/runtime-logs/")
        mock_request.assert_called_once()
        self.assertIn("remotedep123", mock_request.call_args[1]['path'])

    @patch('apps.deployments.services.remote_orchestrator.RemoteOrchestrator._request')
    def test_restart_routed_to_active_host(self, mock_request):
        self.client.post(f"/api/v1/services/{self.service.id}/restart/")
        mock_request.assert_called_once()
        self.assertIn("restart", mock_request.call_args[1]['path'])
