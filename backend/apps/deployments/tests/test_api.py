# pylint: disable=invalid-name
"""Test Api module."""
from django.urls import reverse
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase
from apps.deployments.models import Service, Deployment


class ServiceTests(APITestCase):
    def setUp(self):
        # Create a test user and authenticate
        self.user = User.objects.create_user('testuser', 'test@example.com', 'password123')
        self.client.force_authenticate(user=self.user)

    def test_create_service(self):
        url = reverse('service-list')
        data = {
            'name': 'my-cool-app',
            'repository_url': 'https://github.com/example/app',
            'branch': 'main'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Service.objects.count(), 1)
        self.assertEqual(Service.objects.get().name, 'my-cool-app')

    def test_create_deployment(self):
        service = Service.objects.create(
            name='test-app',
            repository_url='https://github.com/test/app',
            owner=self.user  # Ensure owner matches authenticated user
        )
        url = reverse('deployment-list')
        data = {
            'service': service.id,
            'commit_hash': 'abc1234'
        }
        # Mocking Celery would be needed here for full isolation,
        # but in local test run it might just queue it up or fail if broker missing.
        # We assume CELERY_TASK_ALWAYS_EAGER=True in test settings usually.

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Deployment.objects.count(), 1)
