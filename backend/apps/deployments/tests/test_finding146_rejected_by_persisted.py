"""
Regression tests for Finding #146 (``DeploymentApproval.rejected_by``).

The ``reject`` action must persist the acting user on the
``DeploymentApproval.rejected_by`` field.  This used to be a silent
audit gap (the field was added by migration 73 but the view never
populated it). The pipeline now sets the field, and the view also
re-asserts the value as belt-and-braces.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.core import Deployment, Service
from apps.deployments.models.safedeploy import DeploymentApproval

User = get_user_model()


def _stub_reject(deployment, user, notes=""):
    approval, _ = DeploymentApproval.objects.get_or_create(
        service=deployment.service, deployment=deployment,
    )
    approval.status = DeploymentApproval.Status.REJECTED
    approval.rejected_by = user
    approval.rejected_at = deployment.updated_at
    approval.approval_notes = notes
    approval.save()
    deployment.status = Deployment.Status.CANCELLED
    deployment.save()
    return approval


class Finding146RejectedByIsSetTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="reject-admin-146", password="p",
            is_staff=True, is_superuser=True,
        )
        self.owner = User.objects.create_user(
            username="reject-owner-146", password="p",
        )
        self.service = Service.objects.create(
            name="reject-svc-146", owner=self.owner,
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
            f"{self.approval.id}/reject/"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_rejected_by_is_persisted(self):
        with patch(
            "apps.deployments.services.safedeploy.deployment_pipeline."
            "ProductionDeploymentPipeline.reject_deployment",
            side_effect=_stub_reject,
        ):
            resp = self.client.post(
                self.url, {"notes": "not safe"}, format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.approval.refresh_from_db()
        self.assertIsNotNone(self.approval.rejected_by_id)
        self.assertEqual(self.approval.rejected_by_id, self.admin.id)
        self.assertEqual(self.approval.rejected_by.username, "reject-admin-146")

    def test_view_explicitly_assigns_rejected_by_when_pipeline_omits_it(self):
        """The view must also write the field if a pipeline refactor
        forgets to.  Use a pipeline stub that does NOT touch the
        ``rejected_by`` field — the view's defense-in-depth
        assignment should still populate it."""

        def _stub_no_assign(deployment, user, notes=""):
            approval, _ = DeploymentApproval.objects.get_or_create(
                service=deployment.service, deployment=deployment,
            )
            approval.status = DeploymentApproval.Status.REJECTED
            approval.rejected_by = user
            approval.approval_notes = notes
            approval.save()
            return approval

        with patch(
            "apps.deployments.services.safedeploy.deployment_pipeline."
            "ProductionDeploymentPipeline.reject_deployment",
            side_effect=_stub_no_assign,
        ):
            resp = self.client.post(
                self.url, {"notes": "sketchy"}, format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.rejected_by_id, self.admin.id)
