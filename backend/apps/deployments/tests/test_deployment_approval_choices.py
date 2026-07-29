"""
Regression tests for Issue 48.

``DeploymentApproval.status`` must validate the value against
``DeploymentApproval.Status.choices``. The field is declared with
``choices=Status.choices`` in ``models_safedeploy.py``; this test
locks in the behavior so a future refactor can't drop the validator
silently.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.safedeploy import DeploymentApproval

User = get_user_model()


class DeploymentApprovalStatusChoicesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="approval-choices", password="p",
        )
        self.service = Service.objects.create(
            name="approval-choices-svc",
            owner=self.user,
        )

    def test_default_status_is_pending(self):
        approval = DeploymentApproval.objects.create(service=self.service)
        self.assertEqual(approval.status, DeploymentApproval.Status.PENDING)

    def test_valid_status_choices_accepted(self):
        for valid in (
            DeploymentApproval.Status.PENDING,
            DeploymentApproval.Status.APPROVED,
            DeploymentApproval.Status.REJECTED,
            DeploymentApproval.Status.EXPIRED,
            DeploymentApproval.Status.AUTO_APPROVED,
        ):
            approval = DeploymentApproval(
                service=self.service, status=valid,
            )
            # full_clean() runs the model-level validators, including
            # the choices check.
            try:
                approval.full_clean()
            except ValidationError as exc:
                self.fail(
                    f"Status {valid!r} unexpectedly rejected: {exc}"
                )

    def test_invalid_status_rejected_by_full_clean(self):
        approval = DeploymentApproval(
            service=self.service, status="BOGUS_STATUS",
        )
        with self.assertRaises(ValidationError) as ctx:
            approval.full_clean()
        self.assertIn("status", ctx.exception.message_dict)
