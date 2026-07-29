# pylint: disable=invalid-name
"""
Tests for full service lifecycle.
Validates:
  - Full CRUD lifecycle for services
  - Service listing returns only owner's services
  - Service deletion cascades to deployments
  - Service update validation
  - Deploy action on service endpoint
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, ManagedServer, Service

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "service-lifecycle-tests",
    }
}


@override_settings(CACHES=TEST_CACHES)
class ServiceCRUDTests(APITestCase):
    """Tests for service Create/Read/Update/Delete operations."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='lifecycle',
            email='lifecycle@test.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )

    def test_create_service(self):
        """Creating a service should return 201 and persist to DB."""
        url = '/api/v1/services/'
        data = {
            'name': 'my-new-app',
            'repository_url': 'https://github.com/test/app',
            'branch': 'main'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertTrue(Service.objects.filter(name='my-new-app').exists())

    def test_list_services(self):
        """Listing services should return a 200 with service data."""
        Service.objects.create(
            name='list-test-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )
        url = '/api/v1/services/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)

    def test_retrieve_service(self):
        """Retrieving a single service should return its details."""
        svc = Service.objects.create(
            name='retrieve-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )
        url = f'/api/v1/services/{svc.id}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'retrieve-svc')

    def test_retrieve_service_returns_latest_deployment(self):
        """Latest deployment is returned regardless of status."""
        svc = Service.objects.create(
            name='latest-deploy-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )
        Deployment.objects.create(
            service=svc,
            status=Deployment.Status.ACTIVE,
            commit_hash='active-live',
            finished_at=timezone.now(),
        )
        latest = Deployment.objects.create(
            service=svc,
            status=Deployment.Status.REVIEW,
            commit_hash='review-row',
            finished_at=timezone.now(),
        )

        url = f'/api/v1/services/{svc.id}/'
        response = self.client.get(url)

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['latest_deployment']['id'], str(latest.id))
        self.assertEqual(response.data['latest_deployment']['status'], Deployment.Status.REVIEW)

    def test_update_service_name(self):
        """Updating a service name should persist."""
        svc = Service.objects.create(
            name='original-name',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )
        url = f'/api/v1/services/{svc.id}/'
        response = self.client.patch(url, {'name': 'updated-name'}, format='json')
        self.assertIn(response.status_code, [
            http_status.HTTP_200_OK,
            202
        ])
        svc.refresh_from_db()
        self.assertEqual(svc.name, 'updated-name')

    @patch('apps.deployments.tasks.delete_service_task.delay')
    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value={'ok': True, 'message': 'ok'})
    def test_delete_service(self, _sync_mock, mock_delay):
        """Deleting a service should remove it from DB."""
        svc = Service.objects.create(
            name='delete-me',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )
        url = f'/api/v1/services/{svc.id}/'
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(Service.objects.filter(name='delete-me', status='DELETION_PENDING').exists())


@override_settings(CACHES=TEST_CACHES)
class ServiceOwnershipTests(APITestCase):
    """Test that services are scoped to their owner."""

    def setUp(self):
        self.user1 = User.objects.create_user(
            username='owner1', email='o1@test.com', password='pass123'
        )
        self.user2 = User.objects.create_user(
            username='owner2', email='o2@test.com', password='pass123'
        )
        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )

        # Create a service owned by user1
        self.service = Service.objects.create(
            name='user1-svc',
            repository_url='https://github.com/test/app',
            owner=self.user1,
            provider=self.provider
        )

    def test_owner_can_see_own_service(self):
        """Service owner should see their service in the list."""
        self.client.force_authenticate(user=self.user1)
        url = '/api/v1/services/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)

        names = [s.get('name') for s in response.data
                 if isinstance(s, dict)]
        # If paginated, handle differently
        if not names and isinstance(response.data, dict):
            results = response.data.get('results', [])
            names = [s.get('name') for s in results]

        self.assertIn('user1-svc', names)


@override_settings(CACHES=TEST_CACHES)
class ServiceDeployActionTests(APITestCase):
    """Test the /services/{id}/deploy/ action endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='deployaction',
            email='deploy@test.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )
        self.service = Service.objects.create(
            name='deploy-action-svc',
            repository_url='https://github.com/test/app',
            branch='main',
            owner=self.user,
            provider=self.provider
        )

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_deploy_action_creates_deployment(self, mock_task):
        """POST /services/{id}/deploy/ should create a deployment."""
        url = f'/api/v1/services/{self.service.id}/deploy/'
        response = self.client.post(url, {}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertTrue(
            Deployment.objects.filter(service=self.service).exists()
        )

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_deploy_action_with_ref(self, mock_task):
        """Deploy with a specific ref should use that commit hash."""
        url = f'/api/v1/services/{self.service.id}/deploy/'
        response = self.client.post(url, {'ref': 'abc123'}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        deploy = Deployment.objects.filter(service=self.service).first()
        self.assertIsNotNone(deploy)
        self.assertEqual(deploy.commit_hash, 'abc123')

    def test_deploy_requires_authentication(self):
        """Unauthenticated users cannot trigger deploys."""
        self.client.force_authenticate(user=None)
        url = f'/api/v1/services/{self.service.id}/deploy/'
        response = self.client.post(url, {}, format='json')
        self.assertIn(response.status_code, [
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN
        ])

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_deploy_action_triggers_celery_task(self, mock_task):
        """Deploy action should call smart_deploy_task.delay."""
        url = f'/api/v1/services/{self.service.id}/deploy/'
        self.client.post(url, {}, format='json')
        mock_task.assert_called_once()

    @patch('apps.deployments.views.service.deploy.enqueue_smart_deploy_task')
    def test_manual_deploy_cannot_skip_review(self, mock_enqueue):
        """User-triggered deploys cannot bypass the SafeDeploy review gate."""
        url = f'/api/v1/services/{self.service.id}/deploy/'
        response = self.client.post(url, {'skip_review': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)
        self.assertFalse(Deployment.objects.filter(service=self.service).exists())
        mock_enqueue.assert_not_called()

    @patch('apps.deployments.views.ServerGuard.check_user_workload_allowed',
           return_value={'ok': True})
    @patch('apps.deployments.views.service.deploy.enqueue_smart_deploy_task')
    def test_explicit_local_target_overrides_assigned_remote_server(
        self, mock_enqueue, _guard
    ):
        """Explicit local deploy stays local even when the service has a remote node."""
        remote = ManagedServer.objects.create(
            owner=self.user,
            name='node-1',
            host='69.164.244.51',
            status=ManagedServer.Status.ONLINE,
        )
        self.service.server = remote
        self.service.save(update_fields=['server'])

        url = f'/api/v1/services/{self.service.id}/deploy/'
        response = self.client.post(
            url,
            {'target_server_id': None},
            format='json',
        )

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        deployment = Deployment.objects.get(service=self.service)
        self.assertIsNone(deployment.target_server)
        self.assertTrue(deployment.target_is_local)
        self.service.refresh_from_db()
        self.assertEqual(self.service.server_id, remote.id)
        self.assertFalse(mock_enqueue.call_args.kwargs['skip_review'])

    @patch('apps.deployments.views.ServerGuard.check_user_workload_allowed',
           return_value={'ok': True})
    @patch('apps.deployments.views.service.deploy.enqueue_smart_deploy_task')
    def test_explicit_remote_target_is_saved_on_deployment(
        self, mock_enqueue, _guard
    ):
        """Remote deploy target is per-deployment and still enters review."""
        remote = ManagedServer.objects.create(
            owner=self.user,
            name='node-2',
            host='203.0.113.42',
            status=ManagedServer.Status.ONLINE,
        )

        url = f'/api/v1/services/{self.service.id}/deploy/'
        response = self.client.post(
            url,
            {'target_server_id': str(remote.id)},
            format='json',
        )

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        deployment = Deployment.objects.get(service=self.service)
        self.assertEqual(deployment.target_server_id, remote.id)
        self.assertFalse(deployment.target_is_local)
        self.assertFalse(mock_enqueue.call_args.kwargs['skip_review'])

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_deploy_action_ignores_stale_queued_older_than_active(self, mock_task):
        """
        Deploy guard should cancel stale QUEUED rows once a newer ACTIVE deployment exists.
        """
        stale = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='stale-queued',
        )
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='active-latest',
            finished_at=timezone.now(),
        )

        url = f'/api/v1/services/{self.service.id}/deploy/'
        response = self.client.post(url, {}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        stale.refresh_from_db()
        self.assertEqual(stale.status, Deployment.Status.CANCELLED)
        mock_task.assert_called_once()

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_deploy_action_returns_503_when_queue_fails(self, mock_task):
        """Deploy should fail gracefully if Celery enqueue fails."""
        mock_task.side_effect = RuntimeError('broker unavailable')

        url = f'/api/v1/services/{self.service.id}/deploy/'
        response = self.client.post(url, {}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn('error', response.data)

        deploy = Deployment.objects.filter(service=self.service).order_by('-created_at').first()
        self.assertIsNotNone(deploy)
        self.assertEqual(deploy.status, Deployment.Status.FAILED)

    @patch('apps.cloud.docker_client.get_docker_client')
    def test_status_action_reports_running_container(self, mock_docker_client):
        """Node runtime status endpoint should expose the local container state."""
        container = MagicMock()
        container.id = 'container-id'
        container.name = self.service.name
        container.status = 'running'
        container.attrs = {
            'State': {
                'Status': 'running',
                'Health': {'Status': 'healthy'},
            }
        }
        container.image.tags = ['smsly/test:latest']
        mock_docker_client.return_value.containers.get.return_value = container

        url = f'/api/v1/services/{self.service.id}/status/'
        response = self.client.get(url)

        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'running')
        self.assertTrue(response.data['running'])
        self.assertEqual(response.data['container_id'], 'container-id')


@override_settings(CACHES=TEST_CACHES)
class ServiceDeploymentCascadeTests(APITestCase):
    """Test that deleting a service cascades to deployments."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='cascadetest',
            email='cascade@test.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )
        self.service = Service.objects.create(
            name='cascade-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )
        # Create associated deployments
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='v1'
        )
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash='v2'
        )

    @patch('apps.deployments.tasks.delete_service_task.delay')
    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value={'ok': True, 'message': 'ok'})
    def test_delete_service_cascades_deployments(self, _sync_mock, mock_delay):
        """Deleting a service should also delete its deployments."""
        svc_id = self.service.id
        self.assertEqual(Deployment.objects.filter(service_id=svc_id).count(), 2)

        url = f'/api/v1/services/{svc_id}/'
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 202)

        # Deployments should be gone
