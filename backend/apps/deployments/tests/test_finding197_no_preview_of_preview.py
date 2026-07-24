"""
Regression tests for Finding #197 (no preview-of-preview).

``BranchPreviewManager.create_preview`` must reject a request when
the parent ``service`` is itself a preview environment. Otherwise a
tenant could spin up chains of transient services that escape the
parent's resource limits and quota.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.core import Service
from apps.deployments.services.safedeploy.branch_preview_manager import (
    BranchPreviewManager,
)

User = get_user_model()


class Finding197PreviewOfPreviewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="preview-owner-197", password="p",
        )
        self.manager = BranchPreviewManager()

    def test_create_preview_rejects_is_preview_service(self):
        preview_service = Service.objects.create(
            name="preview-svc-197", owner=self.owner, is_preview=True,
        )
        with self.assertRaises(ValueError) as ctx:
            self.manager.create_preview(
                preview_service, "main", "deadbeef", user=self.owner,
            )
        self.assertIn("preview", str(ctx.exception).lower())

    def test_create_preview_succeeds_for_normal_service(self):
        normal_service = Service.objects.create(
            name="normal-svc-197", owner=self.owner, is_preview=False,
        )
        preview = self.manager.create_preview(
            normal_service, "main", "abc1234", user=self.owner,
        )
        self.assertIsNotNone(preview.id)
        self.assertEqual(preview.status, "PENDING")

    def test_create_preview_default_is_preview_false(self):
        """Sanity: a freshly-created service is not itself a preview."""
        svc = Service.objects.create(name="fresh-svc-197", owner=self.owner)
        self.assertFalse(svc.is_preview)
