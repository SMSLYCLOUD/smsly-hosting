# pylint: disable=invalid-name
"""API tests for nested volumes endpoint hardening."""

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service


class StorageApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='storage-user',
            email='storage-user@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='storage-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='storage-svc',
            owner=self.user,
            provider=self.provider,
        )
        self.client.force_authenticate(user=self.user)

    def test_list_volumes_with_invalid_service_id_returns_empty_list(self):
        response = self.client.get('/api/v1/services/not-a-uuid/volumes/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('count'), 0)
        self.assertEqual(response.data.get('results'), [])

    def test_create_volume_with_invalid_service_id_returns_404(self):
        response = self.client.post(
            '/api/v1/services/not-a-uuid/volumes/',
            {
                'name': 'data',
                'mount_path': '/data',
                'size_gb': 1,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
