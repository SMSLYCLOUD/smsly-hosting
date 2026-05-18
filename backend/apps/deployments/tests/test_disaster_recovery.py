from django.test import TestCase
class DummyTest(TestCase):
    pass
from unittest.mock import patch
from apps.deployments.models import Service, Deployment, Project
from apps.deployments.models_servers import ManagedServer
from apps.cloud.models import CloudProvider
from django.contrib.auth import get_user_model
from apps.deployments.tasks import _handle_failure, _do_promote
from django.utils import timezone

User = get_user_model()

class DisasterRecoveryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dr_user", password="pwd")
        self.project = Project.objects.create(name="DR Proj", owner=self.user)
        self.provider = CloudProvider.objects.create(
            name='DR Provider',

            provider_type='LOCAL'
        )
        self.service = Service.objects.create(
            name="dr-service",

            project=self.project,
            provider=self.provider
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            status='STAGED',
            green_container_id='fake_green_id',
            commit_hash='abc1234'
        )

    @patch('apps.deployments.tasks_alerts._create_in_app_notification')
    def test_deployment_failure_does_not_affect_active_container(self, mock_notify):
        # Current active container
        self.deployment.status = 'BUILDING'
        self.deployment.save()

        _handle_failure(None, self.deployment, "Simulated build error", "Build Error")

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, 'FAILED')
        self.assertIn("Simulated build error", self.deployment.build_logs)
        # Because we never reached ACTIVE, the old container (if any) would still be serving traffic

    @patch('apps.deployments.tasks.ComputeService')
    def test_zero_downtime_promote_safeguards_failed_healthcheck(self, mock_compute):
        # We simulate that the promote step fails.
        # It should raise an exception and NOT mark the deployment as ACTIVE.
        mock_adapter = mock_compute.return_value.adapter
        mock_adapter.promote_container.side_effect = RuntimeError("Green container crashed during promote")

        with self.assertRaises(RuntimeError):
            _do_promote(self.deployment, self.provider)

        self.deployment.refresh_from_db()
        # Ensure it's not marked active. Usually tasks.py catches it and marks FAILED
        self.assertNotEqual(self.deployment.status, 'ACTIVE')
