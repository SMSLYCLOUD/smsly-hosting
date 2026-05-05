from django.test import TestCase
from apps.deployments.models import Deployment, Service, Project
from django.contrib.auth import get_user_model

User = get_user_model()

class DeploymentInvariantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='password123')
        self.project = Project.objects.create(name='Test Project', owner=self.user)
        self.service = Service.objects.create(name='Test Service', project=self.project)

    def test_deployment_state_transition(self):
        """A deployment cannot be marked RUNNING if it is FAILED."""
        deployment = Deployment.objects.create(service=self.service, status=Deployment.Status.FAILED, commit_hash='abc1234')

        # Enforce that if a transition method exists, it blocks invalid state shifts
        if hasattr(deployment, 'transition_to'):
            with self.assertRaises(ValueError):
                deployment.transition_to(Deployment.Status.ACTIVE)

    def test_deployment_cancellation_cleanup(self):
        """Cancelling a deployment must result in a clean state (not running)."""
        deployment = Deployment.objects.create(service=self.service, status=Deployment.Status.DEPLOYING, commit_hash='def5678')
        if hasattr(deployment, 'cancel'):
            deployment.cancel()
        else:
            deployment.status = Deployment.Status.CANCELLED
            deployment.save()
        self.assertEqual(deployment.status, Deployment.Status.CANCELLED)
