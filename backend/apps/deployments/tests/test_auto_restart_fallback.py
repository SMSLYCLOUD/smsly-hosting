# pylint: disable=invalid-name
"""
Tests that the auto-restart path falls back to the most recent SUCCESSFUL
deployment rather than the most recent (possibly broken) commit.

Behavior contract:
  * 0 successful deploys  -> auto-restart is skipped, service flagged
                            ``needs_manual_intervention``.
  * 1 successful + 1 failed -> auto-restart uses the successful commit.
  * Fallback commit fails  -> service marked ``needs_manual_intervention``.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.core.services import health_monitor as hm


class AutoRestartFallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="restart-user",
            email="r@example.com",
            password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="local-fallback",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="restart-svc",
            owner=self.user,
            provider=self.provider,
            health_check_path="/health",
            health_check_interval=30,
            health_check_timeout=1,
            health_check_retries=2,
            auto_restart=True,
        )
        hm.reset_restart_state(str(self.service.id))

    def tearDown(self):
        hm.reset_restart_state(str(self.service.id))
        cache.clear()

    @patch("apps.deployments.tasks.enqueue_smart_deploy_task")
    def test_no_successful_deploys_skips_auto_restart(self, deploy_delay_mock):
        """
        With zero ACTIVE deployments we must NOT queue a new deployment.
        The service should be flagged for manual intervention so an operator
        notices the loop is broken.
        """
        Deployment.objects.create(
            service=self.service,
            commit_hash="badcommit1",
            status=Deployment.Status.FAILED,
        )
        Deployment.objects.create(
            service=self.service,
            commit_hash="badcommit2",
            status=Deployment.Status.FAILED,
        )

        result = hm._trigger_restart(self.service, str(self.service.id))

        self.assertFalse(result)
        deploy_delay_mock.assert_not_called()
        self.service.refresh_from_db()
        self.assertEqual(self.service.health_status, "needs_manual_intervention")

    @patch("apps.deployments.tasks.enqueue_smart_deploy_task")
    def test_falls_back_to_last_successful_commit(self, deploy_delay_mock):
        """
        1 successful deploy (commit A) and 1 failed deploy (commit B).
        Auto-restart should use commit A, not commit B.
        """
        Deployment.objects.create(
            service=self.service,
            commit_hash="commitA",
            status=Deployment.Status.ACTIVE,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        Deployment.objects.create(
            service=self.service,
            commit_hash="commitB",
            status=Deployment.Status.FAILED,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )

        result = hm._trigger_restart(self.service, str(self.service.id))

        self.assertTrue(result)
        deploy_delay_mock.assert_called_once()
        new_deploy_id = deploy_delay_mock.call_args.args[0]
        new_deploy = Deployment.objects.get(id=new_deploy_id)
        self.assertEqual(new_deploy.commit_hash, "commitA")

    @patch("apps.deployments.tasks.enqueue_smart_deploy_task")
    def test_fallback_commit_failure_marks_manual_intervention(
        self, deploy_delay_mock
    ):
        """
        When the fallback (last successful) commit also fails, the service
        must transition to ``needs_manual_intervention`` so the user is
        not stuck in a crash loop.
        """
        Deployment.objects.create(
            service=self.service,
            commit_hash="commitA",
            status=Deployment.Status.ACTIVE,
            started_at=timezone.now(),
            finished_at=timezone.now(),
        )
        self.service.health_status = "unhealthy"
        self.service.save(update_fields=["health_status"])

        first = hm._trigger_restart(self.service, str(self.service.id))
        self.assertTrue(first)
        deploy_delay_mock.assert_called_once()

        fallback_id = deploy_delay_mock.call_args.args[0]
        fallback_deploy = Deployment.objects.get(id=fallback_id)
        fallback_deploy.status = Deployment.Status.FAILED
        fallback_deploy.finished_at = timezone.now()
        fallback_deploy.save(update_fields=["status", "finished_at"])

        second = hm._trigger_restart(self.service, str(self.service.id))
        self.assertFalse(second)

        self.service.refresh_from_db()
        self.assertEqual(self.service.health_status, "needs_manual_intervention")
