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
            build_logs="Error: JavaScript heap out of memory"
        )

    def test_diagnosis_generation(self):
        # We need a detail route for deployment-diagnose
        # Assuming router automatically creates deployment-diagnose if using @action(detail=True)
        # Standard DRF router format: basename-action
        url = reverse(
            'deployments-diagnose',
            kwargs={
                'pk': self.deployment.id})

        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("diagnosis", response.data)
        self.assertIn("ran out of memory", response.data['diagnosis'])

        self.deployment.refresh_from_db()
        self.assertIsNotNone(self.deployment.ai_diagnosis)
