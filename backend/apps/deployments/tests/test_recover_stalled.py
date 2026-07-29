# pylint: disable=invalid-name
"""
Tests that ``recover_stalled_queued_deployments`` does not re-publish a
task that is still in flight on a worker.

Behavior contract:
  * Deployment in ``QUEUED`` whose Celery task is ``STARTED`` -> skipped.
  * Deployment in ``QUEUED`` whose task is ``FAILURE``/absent -> re-queued.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.tasks.deployment.tasks_deploy import recover_stalled_queued_deployments


class RecoverStalledTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="stalled-user",
            password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="stalled-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="stalled-svc",
            owner=self.user,
            provider=self.provider,
        )

    @patch("celery.result.AsyncResult")
    @patch("apps.deployments.tasks.enqueue_smart_deploy_task")
    def test_started_task_is_skipped(self, enqueue_mock, async_result_mock):
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash="abc1234",
        )
        async_result_mock.return_value.state = "STARTED"

        result = recover_stalled_queued_deployments()

        self.assertEqual(result["seen"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["queued"], 0)
        enqueue_mock.assert_not_called()

    @patch("celery.result.AsyncResult")
    @patch("apps.deployments.tasks.enqueue_smart_deploy_task")
    def test_failure_state_is_requeued(self, enqueue_mock, async_result_mock):
        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash="def5678",
        )
        async_result_mock.return_value.state = "FAILURE"

        result = recover_stalled_queued_deployments()

        self.assertEqual(result["seen"], 1)
        self.assertEqual(result["queued"], 1)
        enqueue_mock.assert_called_once_with(
            deployment_id=str(deployment.id),
            provider_id=str(self.provider.id),
            skip_review=False,
        )

    @patch("celery.result.AsyncResult")
    @patch("apps.deployments.tasks.enqueue_smart_deploy_task")
    def test_pending_state_is_requeued(self, enqueue_mock, async_result_mock):
        """PENDING means the task ID has no record in the result backend
        (either never published, or already cleared) — re-queue is safe.
        """
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash="ghi9012",
        )
        async_result_mock.return_value.state = "PENDING"

        result = recover_stalled_queued_deployments()

        self.assertEqual(result["seen"], 1)
        self.assertEqual(result["queued"], 1)
        enqueue_mock.assert_called_once()
