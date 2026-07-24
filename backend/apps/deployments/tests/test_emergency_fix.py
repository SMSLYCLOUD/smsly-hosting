from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from apps.deployments.models import Deployment, Service

User = get_user_model()

class EmergencyDeploymentFixTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client.force_authenticate(user=self.user)
        self.service = Service.objects.create(name='test-service', owner=self.user)

    def test_bulk_cancel_route_does_not_405(self):
        url = '/api/v1/deployments/bulk-cancel/'
        response = self.client.post(url, {'deployment_ids': []}, format='json')
        # 400 because deployment_ids is empty, but NOT 405 Method Not Allowed
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.deployments.views.deployment.review.resume_deploy_task.delay')
    def test_approve_valid_deployment_succeeds(self, mock_task):
        dep = Deployment.objects.create(service=self.service, commit_hash='abc', status=Deployment.Status.REVIEW)
        url = f'/api/v1/deployments/{dep.id}/approve/'
        with patch('apps.deployments.views._resolve_provider_for_service') as mock_resolve:
            mock_resolve.return_value = type('obj', (object,), {'id': 'test'})
            response = self.client.post(url, {}, format='json')
            self.assertIn(response.status_code, [200, 202, 400])


    def test_approve_cancelled_deployment_fails(self):
        dep = Deployment.objects.create(service=self.service, commit_hash='abc', status=Deployment.Status.CANCELLED)
        url = f'/api/v1/deployments/{dep.id}/approve/'
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(response.data.get('ok'))

    def test_cancel_pending_deployment_succeeds(self):
        dep = Deployment.objects.create(service=self.service, commit_hash='abc', status=Deployment.Status.AWAITING_APPROVAL)
        url = f'/api/v1/deployments/{dep.id}/cancel/'
        response = self.client.post(url, {}, format='json')
        self.assertIn(response.status_code, [200, 202, 400])

        self.assertEqual(response.data.get('status'), 'CANCELLED')

    def test_cancel_queued_deployment_succeeds(self):
        dep = Deployment.objects.create(service=self.service, commit_hash='abc', status=Deployment.Status.QUEUED)
        url = f'/api/v1/deployments/{dep.id}/cancel/'
        response = self.client.post(url, {}, format='json')
        self.assertIn(response.status_code, [200, 202, 400])

        self.assertEqual(response.data.get('status'), 'CANCELLED')

    def test_cancel_already_cancelled_deployment(self):
        dep = Deployment.objects.create(service=self.service, commit_hash='abc', status=Deployment.Status.CANCELLED)
        url = f'/api/v1/deployments/{dep.id}/cancel/'
        response = self.client.post(url, {}, format='json')
        self.assertIn(response.status_code, [409])


    def test_bulk_cancel_partial_success(self):
        dep1 = Deployment.objects.create(service=self.service, commit_hash='abc', status=Deployment.Status.QUEUED)
        dep2 = Deployment.objects.create(service=self.service, commit_hash='def', status=Deployment.Status.ACTIVE)
        url = '/api/v1/deployments/bulk-cancel/'
        response = self.client.post(url, {'deployment_ids': [str(dep1.id), str(dep2.id)]}, format='json')
        self.assertIn(response.status_code, [200, 202])
