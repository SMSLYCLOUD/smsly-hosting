from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.deployments.models.core import (
    CloudProvider,
    Deployment,
    ManagedServer,
    Service,
)

User = get_user_model()

@override_settings(SMSLY_DISABLE_SIGNATURE_CHECK=True)
class DeploymentIntegrityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpassword')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # Providers
        self.local_provider = CloudProvider.objects.create(
            provider_type=CloudProvider.ProviderType.LOCAL, is_active=True, name="Local"
        )
        self.remote_provider = CloudProvider.objects.create(
            provider_type=CloudProvider.ProviderType.REMOTE, is_active=True, name="Remote"
        )
        self.agent_provider = CloudProvider.objects.create(
            provider_type=CloudProvider.ProviderType.REMOTE, is_active=True, name="Agent"
        )

        self.remote_server = ManagedServer.objects.create(
            name='remote-node', host='10.0.0.5', is_primary=False, owner=self.user
        )
        self.remote_provider.server = self.remote_server
        self.remote_provider.save()

    def test_explicit_remote_target_unavailable_fails(self):
        self.remote_provider.is_active = False
        self.remote_provider.save()

        service = Service.objects.create(name='test-remote', owner=self.user, provider=self.remote_provider)
        response = self.client.post(f"/api/v1/services/{service.id}/restart/", {"force_rebuild": True})

        self.assertEqual(response.status_code, 400)
        self.assertIn("No active cloud provider configured", str(response.json()))

    def test_explicit_local_target_unavailable_fails(self):
        self.local_provider.is_active = False
        self.local_provider.save()

        service = Service.objects.create(name='test-local', owner=self.user, provider=self.local_provider)
        response = self.client.post(f"/api/v1/services/{service.id}/restart/", {"force_rebuild": True})

        self.assertEqual(response.status_code, 400)
        self.assertIn("No active cloud provider configured", str(response.json()))

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_explicit_remote_target_available_succeeds(self, mock_deploy):
        service = Service.objects.create(name='test-remote-avail', owner=self.user, provider=self.remote_provider)
        response = self.client.post(f"/api/v1/services/{service.id}/restart/", {"force_rebuild": True})

        self.assertIn(response.status_code, [200, 201, 202])
        mock_deploy.assert_called_once()
        self.assertEqual(mock_deploy.call_args[1]['provider_id'], str(self.remote_provider.id))

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_explicit_local_target_available_succeeds(self, mock_deploy):
        service = Service.objects.create(name='test-local-avail', owner=self.user, provider=self.local_provider)
        response = self.client.post(f"/api/v1/services/{service.id}/restart/", {"force_rebuild": True})

        self.assertIn(response.status_code, [200, 201, 202])
        self.assertEqual(mock_deploy.call_args[1]['provider_id'], str(self.local_provider.id))

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_no_explicit_target_uses_default_logic(self, mock_deploy):
        service = Service.objects.create(name='test-no-target', owner=self.user)
        response = self.client.post(f"/api/v1/services/{service.id}/restart/", {"force_rebuild": True})

        self.assertIn(response.status_code, [200, 201, 202])
        provider_id = mock_deploy.call_args[1]['provider_id']
        provider = CloudProvider.objects.get(id=provider_id)
        self.assertEqual(provider.provider_type, CloudProvider.ProviderType.REMOTE)

    @patch('apps.deployments.views.service.deploy.enqueue_smart_deploy_task')
    @patch('apps.deployments.views.ServiceViewSet._is_remote_sync_request', return_value=True)
    def test_remote_sync_prebuilt_image_refreshes_stale_remote_service_image(
        self,
        _mock_remote_sync,
        mock_enqueue,
    ):
        service = Service.objects.create(
            name='test-prebuilt-refresh',
            owner=self.user,
            provider=self.local_provider,
            deploy_type='GIT',
            docker_image='10.100.0.1:5000/smsly/test-prebuilt-refresh:old',
        )
        new_image = '10.100.0.1:5000/smsly/test-prebuilt-refresh:4bd993c'

        response = self.client.post(
            f"/api/v1/services/{service.id}/deploy/",
            {
                "ref": "4bd993c",
                "source_node": "controller",
                "image_name": new_image,
                "skip_review": True,
            },
            format="json",
            HTTP_X_SMSLY_REMOTE_SYNC="1",
        )

        self.assertEqual(response.status_code, 200)
        service.refresh_from_db()
        self.assertEqual(service.docker_image, new_image)
        deployment = Deployment.objects.get(id=response.json()["id"])
        self.assertEqual(deployment.source_node, "controller")
        mock_enqueue.assert_called_once()
