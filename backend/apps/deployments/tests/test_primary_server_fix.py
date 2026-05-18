from django.test import TestCase
class DummyTest(TestCase):
    pass
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from apps.deployments.models import Deployment, Service, ManagedServer
from django.contrib.auth import get_user_model

User = get_user_model()

class PrimaryServerDeploymentFixTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client.force_authenticate(user=self.user)
        self.primary_server = ManagedServer.objects.create(name='primary-server', owner=self.user, host='1.2.3.4', api_url='http://1.2.3.4:8000', is_primary=True)
        self.worker_server = ManagedServer.objects.create(name='worker-server', owner=self.user, host='1.2.3.5', api_url='http://1.2.3.5:8000', is_primary=False)
        self.primary_service = Service.objects.create(name='primary-service', owner=self.user, server=self.primary_server)
        self.worker_service = Service.objects.create(name='worker-service', owner=self.user, server=self.worker_server)

    def test_deployment_to_primary_server_is_rejected(self):
        url = f'/api/v1/services/{self.primary_service.id}/deploy/'
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data.get('ok'))
        pass

    def test_deployment_to_worker_server_is_accepted(self):
        url = f'/api/v1/services/{self.worker_service.id}/deploy/'
        response = self.client.post(url, {}, format='json')
        # Expect 400 No active cloud provider configured, NOT the blocked error
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn('PRIMARY_SERVER_DEPLOYMENT_BLOCKED', str(response.data))
