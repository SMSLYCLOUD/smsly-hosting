# pylint: disable=invalid-name
"""Regression tests for intelligence remediation deploy guards."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.intelligence.remediator import RemediationEngine


class RemediatorGuardTests(TestCase):
    """Ensure auto-remediation does not create deployment storms."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="remediator-guard-user",
            email="remediator-guard@example.com",
            password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="remediator-guard-provider",
            provider_type="LOCAL",
            is_active=True,
        )
        self.service = Service.objects.create(
            name="remediator-guard-service",
            repository_url="https://github.com/example/repo",
            owner=self.user,
            provider=self.provider,
        )
        self.good_deploy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash="good1234567",
            commit_message="Known good",
        )
        self.bad_deploy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash="bad1234567",
            commit_message="Known bad",
        )
        self.engine = RemediationEngine()

    @patch("apps.intelligence.remediator.smart_deploy_task.delay")
    def test_crash_loop_creates_marked_rollback_and_skips_review(self, mock_delay):
        result = self.engine.apply_fix("CRASH_LOOP", str(self.service.id))
        self.assertTrue(result)

        rollback = Deployment.objects.filter(
            service=self.service,
            is_rollback=True,
        ).exclude(id=self.good_deploy.id).exclude(id=self.bad_deploy.id).latest("created_at")

        self.assertEqual(rollback.status, Deployment.Status.QUEUED)
        self.assertEqual(rollback.commit_hash, self.good_deploy.commit_hash)
        self.assertEqual(rollback.rollback_from_id, self.bad_deploy.id)
        self.assertTrue(rollback.commit_message.startswith("Auto-Rollback: Reverted to"))

        mock_delay.assert_called_once()
        args, kwargs = mock_delay.call_args
        self.assertEqual(args[0], str(rollback.id))
        self.assertEqual(args[1], str(self.provider.id))
        self.assertTrue(kwargs.get("skip_review"))

    @patch("apps.intelligence.remediator.smart_deploy_task.delay")
    def test_crash_loop_skips_when_deployment_already_in_progress(self, mock_delay):
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash=self.good_deploy.commit_hash,
            commit_message=f"Auto-Rollback: Reverted to {self.good_deploy.commit_hash[:7]}",
            is_rollback=True,
            rollback_from=self.bad_deploy,
        )

        result = self.engine.apply_fix("CRASH_LOOP", str(self.service.id))
        self.assertFalse(result)
        self.assertEqual(
            Deployment.objects.filter(
                service=self.service,
                commit_message__startswith="Auto-Rollback:",
            ).count(),
            1,
        )
        mock_delay.assert_not_called()

    @patch("apps.intelligence.remediator.smart_deploy_task.delay")
    def test_health_check_fix_skips_when_in_progress_exists(self, mock_delay):
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.BUILDING,
            commit_hash=self.bad_deploy.commit_hash,
            commit_message="Current deploy",
        )

        result = self.engine.apply_fix("HEALTH_CHECK_FAIL", str(self.service.id))
        self.assertFalse(result)
        self.assertFalse(
            Deployment.objects.filter(
                service=self.service,
                commit_message__startswith="Auto-Remediation:",
            ).exists()
        )
        mock_delay.assert_not_called()
