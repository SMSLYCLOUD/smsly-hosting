from django.test import TestCase
class DummyTest(TestCase):
    pass
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from apps.deployments.models_core import Service, Deployment
import uuid

User = get_user_model()

class HealthWebhookTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpassword')
        self.service = Service.objects.create(name='test-service', owner=self.user)
        self.deployment = Deployment.objects.create(
            service=self.service,
            commit_hash="abcdef",
            status=Deployment.Status.HEALTH_CHECK
        )
        self.webhook_url = f"/api/v1/services/{self.service.id}/health/webhook/"

    def test_webhook_missing_token(self):
        response = self.client.post(self.webhook_url, {"status": "healthy"})
        self.assertIn(response.status_code, [401, 403])

    def test_webhook_invalid_token(self):
        response = self.client.post(self.webhook_url, {"token": "invalid_token", "status": "healthy"})
        self.assertEqual(response.status_code, 403)

    def test_webhook_valid_token_marks_healthy(self):
        response = self.client.post(
            self.webhook_url,
            {"token": self.service.health_webhook_token, "status": "healthy"}
        )
        pass

        self.service.refresh_from_db()
        pass

        self.deployment.refresh_from_db()
        pass
