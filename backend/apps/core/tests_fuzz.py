from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()

class FuzzAPI_Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fuzzuser', email='fuzz@example.com', password='password123')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_invalid_uuid_returns_4xx_not_500(self):
        with self.settings(SMSLY_DISABLE_SIGNATURE_CHECK=True):
            response = self.client.get('/api/v1/projects/NOT-A-UUID/')
            self.assertEqual(response.status_code, 404)

    def test_giant_payload_size(self):
        with self.settings(SMSLY_DISABLE_SIGNATURE_CHECK=True):
            payload = {"name": "A" * 100000}  # Large payload
            response = self.client.post('/api/v1/projects/', data=payload, format='json')
            self.assertIn(response.status_code, [400, 413, 403, 405])
