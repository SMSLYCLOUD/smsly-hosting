# pylint: disable=invalid-name
"""Test Intelligence module."""
from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from django.contrib.auth.models import User
from apps.deployments.models import Service, Deployment


class AIDiagnosisTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('ai_test', 'ai@test.com', 'pass')
        self.client.force_authenticate(user=self.user)
        self.service = Service.objects.create(name='ai-app', owner=self.user)
        self.deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            build_logs="Error: JavaScript heap out of memory",
            commit_hash='abc1234'
        )

    def test_diagnosis_generation(self):
        # We need a detail route for deployment-diagnose
        # Assuming router automatically creates deployment-diagnose if using @action(detail=True)
        # Standard DRF router format: basename-action
        # UPDATED: 'deployments-diagnose' -> 'deployment-diagnose' to match basename='deployment'
        url = reverse(
            'deployment-diagnose',
            kwargs={
                'pk': self.deployment.id})

        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify the async task trigger message
        self.assertEqual(response.data['message'], 'Analysis started')
