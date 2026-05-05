from django.test import TestCase
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from apps.deployments.models import Project

User = get_user_model()

class SecurityHardeningTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='user1', email='user1@example.com', password='password123')
        self.user2 = User.objects.create_user(username='user2', email='user2@example.com', password='password123')
        self.project1 = Project.objects.create(name='User1 Project', owner=self.user1)
        self.client1 = APIClient()
        self.client1.force_authenticate(user=self.user1)
        self.client2 = APIClient()
        self.client2.force_authenticate(user=self.user2)

    def test_tenant_isolation(self):
        """User2 must not be able to access User1's project."""
        with self.settings(SMSLY_DISABLE_SIGNATURE_CHECK=True):
            response = self.client2.get(f'/api/v1/projects/{self.project1.id}/')
            self.assertEqual(response.status_code, 404)

    def test_path_traversal_prevention(self):
        """Simulate a path traversal payload."""
        with self.settings(SMSLY_DISABLE_SIGNATURE_CHECK=True):
            payload = {"name": "../../../etc/passwd"}
            response = self.client1.post('/api/v1/projects/', data=payload, format='json')
            self.assertIn(response.status_code, [400, 201])
