# pylint: disable=invalid-name
"""
Tests for the deployment orchestrator.
Validates:
  - Deployment status transitions (QUEUED → BUILDING → DEPLOYING → ACTIVE)
  - Failure handling and status updates
  - Deployment timeout enforcement
  - Auto-rollback after consecutive failures
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service


class OrchestratorStatusTransitionTests(TestCase):
    """Test deployment status transitions through the orchestrator."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='orchestrator_test',
            email='orch@test.com',
            password='testpass123'
        )
        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )
        self.service = Service.objects.create(
            name='orch-test-svc',
            repository_url='https://github.com/test/app',
            branch='main',
            owner=self.user,
            provider=self.provider,
            # The new centralized engine defaults to a rolling-window
            # threshold of 5 in 30 min. Pin the test service to the old
            # consecutive-failures behavior so existing assertions hold.
            auto_rollback_enabled=True,
            auto_rollback_threshold=3,
        )

    @patch('apps.deployments.services.orchestrator.ClusterManager')
    @patch('apps.deployments.services.orchestrator.BuildManager')
    def test_successful_deployment_reaches_active(self, MockBuild, MockCluster):
        """A successful deployment should transition to ACTIVE."""
        MockBuild.return_value.build_image.return_value = 'test-image:latest'
        MockCluster.return_value.deploy_service.return_value = 'container-123'

        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='abc123'
        )

        from apps.deployments.services.orchestrator import Orchestrator
        orch = Orchestrator(str(deployment.id))
        orch.run_deployment()

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, Deployment.Status.ACTIVE)
        self.assertEqual(deployment.container_id, 'container-123')
        self.assertIsNotNone(deployment.finished_at)

    @patch('apps.deployments.services.orchestrator.analyze_failure_task')
    @patch('apps.deployments.services.orchestrator.alert_user_task')
    @patch('apps.deployments.services.orchestrator.ClusterManager')
    @patch('apps.deployments.services.orchestrator.BuildManager')
    def test_build_failure_sets_failed_status(
        self, MockBuild, MockCluster, mock_alert, mock_analyze
    ):
        """A build failure should mark the deployment as FAILED."""
        MockBuild.return_value.build_image.side_effect = Exception('Build failed')
        mock_alert.delay = MagicMock()
        mock_analyze.delay = MagicMock()

        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='bad123'
        )

        from apps.deployments.services.orchestrator import Orchestrator
        orch = Orchestrator(str(deployment.id))

        with self.assertRaises(Exception):
            orch.run_deployment()

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, Deployment.Status.FAILED)
        self.assertIn('[ERROR]', deployment.build_logs)

    @patch('apps.deployments.services.orchestrator.analyze_failure_task')
    @patch('apps.deployments.services.orchestrator.alert_user_task')
    @patch('apps.deployments.services.orchestrator.ClusterManager')
    @patch('apps.deployments.services.orchestrator.BuildManager')
    def test_failure_triggers_alert_and_analysis(
        self, MockBuild, MockCluster, mock_alert, mock_analyze
    ):
        """A failure should trigger both SMS alert and AI analysis."""
        MockBuild.return_value.build_image.side_effect = Exception('Crash')
        mock_alert.delay = MagicMock()
        mock_analyze.delay = MagicMock()

        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='crash123'
        )

        from apps.deployments.services.orchestrator import Orchestrator
        orch = Orchestrator(str(deployment.id))

        with self.assertRaises(Exception):
            orch.run_deployment()

        mock_alert.delay.assert_called_once()
        mock_analyze.delay.assert_called_once()

    @patch('apps.deployments.services.orchestrator.analyze_failure_task')
    @patch('apps.deployments.services.orchestrator.alert_user_task')
    @patch('apps.deployments.services.orchestrator.ClusterManager')
    @patch('apps.deployments.services.orchestrator.BuildManager')
    def test_deployment_has_started_at_timestamp(
        self, MockBuild, MockCluster, mock_alert, mock_analyze
    ):
        """Deployment should have started_at set after run_deployment."""
        MockBuild.return_value.build_image.return_value = 'img:1'
        MockCluster.return_value.deploy_service.return_value = 'c-1'

        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='ts123'
        )

        from apps.deployments.services.orchestrator import Orchestrator
        orch = Orchestrator(str(deployment.id))
        orch.run_deployment()

        deployment.refresh_from_db()
        self.assertIsNotNone(deployment.started_at)


class AutoRollbackTests(TestCase):
    """Test auto-rollback logic after consecutive failures."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='autorollback_test',
            email='autorollback@test.com',
            password='testpass123'
        )
        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )
        self.service = Service.objects.create(
            name='autorollback-svc',
            repository_url='https://github.com/test/crashy',
            branch='main',
            owner=self.user,
            provider=self.provider
        )

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    @patch('apps.deployments.services.orchestrator.analyze_failure_task')
    @patch('apps.deployments.services.orchestrator.alert_user_task')
    @patch('apps.deployments.services.orchestrator.ClusterManager')
    @patch('apps.deployments.services.orchestrator.BuildManager')
    def test_auto_rollback_after_consecutive_failures(
        self, MockBuild, MockCluster, mock_alert, mock_analyze, mock_deploy
    ):
        """After 3 consecutive failures, auto-rollback should trigger."""
        mock_alert.delay = MagicMock()
        mock_analyze.delay = MagicMock()
        MockBuild.return_value.build_image.side_effect = Exception('Fail')

        # Create a known-good deployment
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='good_abc',
            commit_message='Good deployment'
        )

        # Create 2 previous failures
        for i in range(2):
            Deployment.objects.create(
                service=self.service,
                status=Deployment.Status.FAILED,
                commit_hash=f'fail_{i}'
            )

        # Create the 3rd failure (triggers auto-rollback)
        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='fail_3'
        )

        from apps.deployments.services.orchestrator import Orchestrator
        orch = Orchestrator(str(deployment.id))

        with self.assertRaises(Exception):
            orch.run_deployment()

        # Auto-rollback should have created a new deployment
        rollback_deploy = (
            Deployment.objects
            .filter(service=self.service, commit_hash='good_abc')
            .exclude(status=Deployment.Status.ACTIVE)
            .first()
        )
        self.assertIsNotNone(rollback_deploy)
        self.assertEqual(rollback_deploy.status, Deployment.Status.QUEUED)
        self.assertIn('AUTO-ROLLBACK', rollback_deploy.commit_message)

    @patch('apps.deployments.services.orchestrator.analyze_failure_task')
    @patch('apps.deployments.services.orchestrator.alert_user_task')
    @patch('apps.deployments.services.orchestrator.ClusterManager')
    @patch('apps.deployments.services.orchestrator.BuildManager')
    def test_no_rollback_with_only_two_failures(
        self, MockBuild, MockCluster, mock_alert, mock_analyze
    ):
        """Auto-rollback should NOT trigger with fewer than 3 failures."""
        mock_alert.delay = MagicMock()
        mock_analyze.delay = MagicMock()
        MockBuild.return_value.build_image.side_effect = Exception('Fail')

        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='good_xyz'
        )

        # Only 1 previous failure
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash='fail_1'
        )

        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='fail_2'
        )

        from apps.deployments.services.orchestrator import Orchestrator
        orch = Orchestrator(str(deployment.id))

        with self.assertRaises(Exception):
            orch.run_deployment()

        # No auto-rollback deployment should exist
        self.assertFalse(
            Deployment.objects
            .filter(commit_message__contains='AUTO-ROLLBACK')
            .exists()
        )
