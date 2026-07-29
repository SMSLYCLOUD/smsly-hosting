from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from apps.deployments.models.core import Service

User = get_user_model()

class ServiceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='apitest', password='testpass')
        self.client.force_authenticate(user=self.user)

    def test_list_services(self):
        resp = self.client.get('/api/v1/services/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_create_service(self):
        resp = self.client.post('/api/v1/services/', {
            'name': 'api-test-svc',
            'repository_url': 'https://github.com/test/repo'
        }, format='json')
        self.assertIn(resp.status_code, [200, 201])

    def test_create_service_missing_name(self):
        resp = self.client.post('/api/v1/services/', {
            'repository_url': 'https://github.com/test/repo'
        }, format='json')
        self.assertIn(resp.status_code, [400, 422])

    def test_retrieve_service(self):
        svc = Service.objects.create(name='retrieve-svc', repository_url='https://github.com/test/repo', owner=self.user)
        resp = self.client.get(f'/api/v1/services/{svc.id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

class AuthAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_unauthenticated_list(self):
        resp = self.client.get('/api/v1/services/')
        self.assertIn(resp.status_code, [401, 403])

    def test_login_valid(self):
        User.objects.create_user(username='loginuser', password='loginpass')
        resp = self.client.post('/api/v1/auth/login/', {'username': 'loginuser', 'password': 'loginpass'}, format='json')
        self.assertIn(resp.status_code, [200, 204])

    def test_login_invalid(self):
        resp = self.client.post('/api/v1/auth/login/', {'username': 'nouser', 'password': 'wrong'}, format='json')
        self.assertIn(resp.status_code, [400, 401])

class DeploymentAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='apidep', password='testpass')
        self.client.force_authenticate(user=self.user)
        self.service = Service.objects.create(name='dep-api-svc', repository_url='https://github.com/test/repo', owner=self.user)

    def test_list_deployments(self):
        resp = self.client.get(f'/api/v1/services/{self.service.id}/deployments/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

class AuditLogAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='audituser', password='testpass')
        self.client.force_authenticate(user=self.user)

    def test_list_audit_logs(self):
        resp = self.client.get('/api/v1/audit-logs/')
        self.assertIn(resp.status_code, [200, 403])
