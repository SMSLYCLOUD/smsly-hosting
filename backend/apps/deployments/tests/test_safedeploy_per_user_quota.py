"""
Regression tests for the per-creator preview quota (Issue 30).

Covers:
  1. The per-user quota blocks a creator who has hit the cap.
  2. Destroyed/expired previews from the same creator do not count
     against the quota.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.views import safedeploy as views_safedeploy
from apps.deployments.models.core import Service
from apps.deployments.models.safedeploy import PreviewEnvironment

User = get_user_model()


class PerUserPreviewQuotaTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="quota-owner", password="p",
        )
        self.service = Service.objects.create(
            name="quota-svc", owner=self.owner,
        )
        self.url = f"/api/v1/services/{self.service.id}/previews/"
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        # Lower the quota for the duration of these tests.
        self._original_quota = views_safedeploy.MAX_PREVIEWS_PER_CREATOR
        views_safedeploy.MAX_PREVIEWS_PER_CREATOR = 2

    def tearDown(self):
        views_safedeploy.MAX_PREVIEWS_PER_CREATOR = self._original_quota

    def _create_preview(self, user, branch="main", sha="abc1234"):
        from apps.deployments.services.safedeploy.branch_preview_manager import (
            BranchPreviewManager,
        )
        return BranchPreviewManager().create_preview(
            self.service, branch, sha, user=user,
        )

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_creator_quota_blocks_after_two_previews(self, mock_delay):
        mock_delay.return_value = None

        p1 = self._create_preview(self.owner, branch="main-1", sha="aaaa111")
        p1.status = PreviewEnvironment.Status.BUILDING
        p1.save()
        p2 = self._create_preview(self.owner, branch="main-2", sha="bbbb222")
        p2.status = PreviewEnvironment.Status.READY
        p2.save()

        resp = self.client.post(
            self.url,
            {"branch_name": "feature-x", "commit_sha": "deadbeef"},
            format="json",
        )
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Per-user preview quota", str(resp.data))

    def test_destroyed_previews_do_not_count_against_quota(self):
        # Two destroyed previews must not trip the quota.
        p1 = self._create_preview(self.owner, branch="main-1", sha="aaaa111")
        p1.status = PreviewEnvironment.Status.DESTROYED
        p1.save()
        p2 = self._create_preview(self.owner, branch="main-2", sha="bbbb222")
        p2.status = PreviewEnvironment.Status.EXPIRED
        p2.save()

        with patch(
            "apps.deployments.tasks_safedeploy.create_preview_environment_job.delay"
        ) as mock_delay:
            mock_delay.return_value = None
            resp = self.client.post(
                self.url,
                {"branch_name": "fresh", "commit_sha": "1234567"},
                format="json",
            )
        self.assertEqual(resp.status_code, 201)

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_third_user_preview_does_not_count_against_first_creator(self, mock_delay):
        mock_delay.return_value = None

        # A different creator has 1 active preview on the same service.
        other = User.objects.create_user(
            username="quota-other", password="p",
        )
        other_preview = self._create_preview(
            other, branch="other-1", sha="cccc333",
        )
        other_preview.status = PreviewEnvironment.Status.READY
        other_preview.save()

        # The owner can still create up to the per-user cap (2) because the
        # quota is per-creator. We promote each owner preview to an active
        # status so they count against the per-creator quota.
        resp1 = self.client.post(
            self.url,
            {"branch_name": "owner-1", "commit_sha": "aaaaaaa1"},
            format="json",
        )
        self.assertEqual(resp1.status_code, 201)
        PreviewEnvironment.objects.filter(
            id=resp1.data["id"],
        ).update(status=PreviewEnvironment.Status.BUILDING)
        resp2 = self.client.post(
            self.url,
            {"branch_name": "owner-2", "commit_sha": "aaaaaaa2"},
            format="json",
        )
        self.assertEqual(resp2.status_code, 201)
        PreviewEnvironment.objects.filter(
            id=resp2.data["id"],
        ).update(status=PreviewEnvironment.Status.BUILDING)
        resp3 = self.client.post(
            self.url,
            {"branch_name": "owner-3", "commit_sha": "aaaaaaa3"},
            format="json",
        )
        self.assertEqual(resp3.status_code, 429)
