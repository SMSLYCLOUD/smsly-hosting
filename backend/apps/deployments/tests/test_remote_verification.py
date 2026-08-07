from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models.core import Deployment, Service

User = get_user_model()

@override_settings(SMSLY_DISABLE_SIGNATURE_CHECK=True)
class RemoteVerificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpassword')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.provider = CloudProvider.objects.create(
            provider_type=CloudProvider.ProviderType.REMOTE,
            is_active=True,
            name="Test Remote"
        )
        self.service = Service.objects.create(
            name='test-service',
            owner=self.user,
            provider=self.provider
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash="abcdef"
        )

    def test_signature_middleware_bypass(self):
        response = self.client.get(f'/api/v1/services/{self.service.id}/')
        self.assertEqual(response.status_code, 200)

        webhook_url = f"/api/v1/services/{self.service.id}/health/webhook/"
        response = self.client.post(webhook_url, {"token": self.service.health_webhook_token, "status": "healthy"})
        self.assertIn(response.status_code, [200, 202])
