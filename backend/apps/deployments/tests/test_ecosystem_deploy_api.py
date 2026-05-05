from django.test import TestCase
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from apps.deployments.models import Project

User = get_user_model()

class EcosystemDeployAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ecosystem_api', email='ecoapi@example.com', password='password123')
        self.project = Project.objects.create(name='Ecosystem API Project', owner=self.user)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_ecosystem_deploy_submission(self):
        with self.settings(SMSLY_DISABLE_SIGNATURE_CHECK=True):
            response = self.client.post(f'/api/v1/projects/{self.project.id}/ecosystem_deploy/')
            self.assertNotEqual(response.status_code, 500)
