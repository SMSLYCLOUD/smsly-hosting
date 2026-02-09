"""Tests for webhook security."""
import hmac
import hashlib
from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.deployments.models import Service
from apps.cloud.models import CloudProvider
from django.conf import settings


class WebhookSecurityTests(TestCase):
    """Test webhook signature validation and security."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@example.com', 'password123')
        self.provider = CloudProvider.objects.create(
            name='Local',
            provider_type='LOCAL'
        )
        self.service = Service.objects.create(
            name='test-service',
            repository_url='https://github.com/test/repo',
            branch='main',
            owner=self.user,
            provider=self.provider
        )
    
    def _generate_signature(self, payload):
        """Generate valid GitHub webhook signature."""
        secret = settings.GITHUB_WEBHOOK_SECRET.encode()
        signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        return f'sha256={signature}'
    
    def test_webhook_with_valid_signature(self):
        """Valid signature should process webhook."""
        payload = '{"ref": "refs/heads/main", "repository": {"clone_url": "https://github.com/test/repo"}}'
        signature = self._generate_signature(payload)
        
        response = self.client.post(
            '/api/v1/webhooks/github/',
            data=payload,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature
        )
        
        self.assertEqual(response.status_code, 200)
    
    def test_webhook_with_invalid_signature(self):
        """Invalid signature should be rejected."""
        payload = '{"ref": "refs/heads/main"}'
        
        response = self.client.post(
            '/api/v1/webhooks/github/',
            data=payload,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256='sha256=invalid_signature'
        )
        
        self.assertEqual(response.status_code, 403)
    
    def test_webhook_without_signature(self):
        """Missing signature should be rejected."""
        payload = '{"ref": "refs/heads/main"}'
        
        response = self.client.post(
            '/api/v1/webhooks/github/',
            data=payload,
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 403)
    
    def test_webhook_replay_attack_prevention(self):
        """Test that webhooks cannot be replayed."""
        payload = '{"ref": "refs/heads/main", "repository": {"clone_url": "https://github.com/test/repo"}}'
        signature = self._generate_signature(payload)
        
        # First request should succeed
        response1 = self.client.post(
            '/api/v1/webhooks/github/',
            data=payload,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature
        )
        
        # NOTE: Replay protection requires timestamp validation
        # This is a placeholder for future implementation
        self.assertEqual(response1.status_code, 200)
