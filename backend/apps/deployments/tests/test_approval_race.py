"""
Regression tests for the approval race (Issue 31).

Covers:
  1. Once an approval is APPROVED, a second approve() returns 409
     (the deployment is in AWAITING_APPROVAL but the approval row is
     no longer PENDING).
  2. The approve() action's source uses ``select_for_update`` and
     ``transaction.atomic`` so concurrent admins cannot clobber state.
"""
import inspect
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.views import safedeploy as views_safedeploy
from apps.deployments.models.core import Deployment, Service
from apps.deployments.models.safedeploy import DeploymentApproval

User = get_user_model()


def _stub_approve_and_process(deployment, user):
    """Test double that flips status to APPROVED without doing real work."""
    approval = DeploymentApproval.objects.filter(deployment=deployment).first()
    if approval is None:
        approval = DeploymentApproval.objects.create(
            service=deployment.service, deployment=deployment,
        )
    approval.status = DeploymentApproval.Status.APPROVED
    approval.approved_by = user
    approval.save(update_fields=["status", "approved_by", "updated_at"])
    return approval


class ApprovalAlreadyApprovedTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin-1", password="p", is_staff=True, is_superuser=True,
        )
        self.owner = User.objects.create_user(
            username="svc-owner", password="p",
        )
        self.service = Service.objects.create(
            name="approval-svc", owner=self.owner,
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 40,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        self.approval = DeploymentApproval.objects.create(
            service=self.service,
            deployment=self.deployment,
            status=DeploymentApproval.Status.PENDING,
        )
        self.url = (
            f"/api/v1/services/{self.service.id}/approvals/"
            f"{self.approval.id}/approve/"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_second_approve_returns_409(self):
        with patch(
            "apps.deployments.services.safedeploy.deployment_pipeline."
            "ProductionDeploymentPipeline.approve_and_process",
            side_effect=_stub_approve_and_process,
        ):
            first = self.client.post(self.url, {}, format="json")
            self.assertEqual(first.status_code, 200)

            second = self.client.post(self.url, {}, format="json")
            self.assertEqual(second.status_code, 409)
            self.assertIn("not PENDING", str(second.data))


class ApproveActionAtomicityTests(TestCase):
    """Static checks on the approve() action source code."""

    def test_approve_action_uses_select_for_update(self):
        source = inspect.getsource(views_safedeploy.DeploymentApprovalViewSet.approve)
        self.assertIn("select_for_update", source)
        self.assertIn("transaction.atomic", source)
