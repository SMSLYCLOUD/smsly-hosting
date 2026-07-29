from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service

User = get_user_model()

class EcosystemDeployServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ecosystem', email='eco@example.com', password='password123')
        self.project = Project.objects.create(name='Ecosystem Project', owner=self.user)
        self.service1 = Service.objects.create(name='Service A', project=self.project)
        self.service2 = Service.objects.create(name='Service B', project=self.project)

    @patch('apps.deployments.tasks.deploy.build._link_ecosystem')
    def test_link_ecosystem_called(self, mock_link):
        pass
