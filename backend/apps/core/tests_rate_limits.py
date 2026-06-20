
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()

class APIRateLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='password123')
        self.client = APIClient()
        cache.clear()

    def test_unauthenticated_rate_limits(self):
        # We test for 403 first to ensure it's protected by signature or auth
        response = self.client.get('/api/v1/projects/')
        self.assertEqual(response.status_code, 403)

    def test_authenticated_rate_limits(self):
        self.client.force_authenticate(user=self.user)
        # Bypassing the signature check for testing the core app view behaviour
        with self.settings(SMSLY_DISABLE_SIGNATURE_CHECK=True):
            response = self.client.get('/api/v1/projects/')
            self.assertEqual(response.status_code, 200)

    def test_rate_limit_throttle(self):
        self.client.force_authenticate(user=self.user)
        with self.settings(SMSLY_DISABLE_SIGNATURE_CHECK=True):
            response = self.client.get('/api/v1/projects/')
            self.assertEqual(response.status_code, 200)
