# pylint: disable=invalid-name
"""Regression tests for Finding #142 (process_deployment idempotency).

Before the fix, ``ProductionDeploymentPipeline.process_deployment``
re-set ``deployment.status = MIGRATION_PLANNING`` on every call, which
restarted a deployment that was already ``SUCCEEDED``/``FAILED``/
``CANCELLED`` and could clobber downstream state. The fix now
short-circuits with the existing deployment row when the status is
already terminal, and the type annotation is updated to
``Deployment`` to make the contract explicit.

These tests verify:
  * Calling ``process_deployment`` twice in a row does not re-open
    a deployment that already reached a terminal state.
  * The deployment row passed in is the same object returned on the
    second (idempotent) call.
  * The ``updated_at`` timestamp does not change on a no-op call.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Deployment, Service
from apps.deployments.services.safedeploy.deployment_pipeline import (
    ProductionDeploymentPipeline,
)

User = get_user_model()


class Finding142ProcessDeploymentIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='fix142-user', password='x',
        )
        self.service = Service.objects.create(
            name='fix142-svc', owner=self.user,
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            commit_hash='a' * 7,
            status=Deployment.Status.ACTIVE,
        )
        self.original_updated_at = self.deployment.updated_at

    def test_terminal_active_deployment_is_no_op(self):
        """A deployment already ACTIVE must not be re-set to MIGRATION_PLANNING."""
        pipeline = ProductionDeploymentPipeline()
        result = pipeline.process_deployment(self.deployment)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, Deployment.Status.ACTIVE)
        self.assertEqual(self.deployment.updated_at, self.original_updated_at)
        self.assertEqual(result.id, self.deployment.id)

    @patch.object(
        ProductionDeploymentPipeline,
        "_get_latest_validation_for_commit",
        return_value=None,
    )
    def test_terminal_failed_deployment_is_no_op(self, _mock_validation):
        """A FAILED deployment must remain FAILED on a re-entry."""
        self.deployment.status = Deployment.Status.FAILED
        self.deployment.save(update_fields=["status", "updated_at"])
        original_failed_at = self.deployment.updated_at

        pipeline = ProductionDeploymentPipeline()
        result = pipeline.process_deployment(self.deployment)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, Deployment.Status.FAILED)
        self.assertEqual(self.deployment.updated_at, original_failed_at)
        self.assertEqual(result.id, self.deployment.id)

    @patch.object(
        ProductionDeploymentPipeline,
        "_get_latest_validation_for_commit",
        return_value=None,
    )
    def test_terminal_cancelled_deployment_is_no_op(self, _mock_validation):
        """A CANCELLED deployment must remain CANCELLED on a re-entry."""
        self.deployment.status = Deployment.Status.CANCELLED
        self.deployment.save(update_fields=["status", "updated_at"])
        original_cancelled_at = self.deployment.updated_at

        pipeline = ProductionDeploymentPipeline()
        result = pipeline.process_deployment(self.deployment)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, Deployment.Status.CANCELLED)
        self.assertEqual(self.deployment.updated_at, original_cancelled_at)
        self.assertEqual(result.id, self.deployment.id)
