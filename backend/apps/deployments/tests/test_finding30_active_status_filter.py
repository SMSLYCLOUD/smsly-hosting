from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.views import safedeploy as views_safedeploy
from apps.deployments.models.core import Service
from apps.deployments.models.safedeploy import PreviewEnvironment

User = get_user_model()


class Finding30ActiveStatusFilterTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="f30-owner", password="p",
        )
        self.service = Service.objects.create(
            name="f30-svc", owner=self.owner,
            preview_environments_enabled=True,
        )
        self.url = f"/api/v1/services/{self.service.id}/previews/"
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self._original_quota = views_safedeploy.MAX_PREVIEWS_PER_CREATOR
        views_safedeploy.MAX_PREVIEWS_PER_CREATOR = 2

    def tearDown(self):
        views_safedeploy.MAX_PREVIEWS_PER_CREATOR = self._original_quota

    def _create_preview(self, user, branch, sha):
        from apps.deployments.services.safedeploy.branch_preview_manager import (
            BranchPreviewManager,
        )
        return BranchPreviewManager().create_preview(
            self.service, branch, sha, user=user,
        )

    def _set_status(self, preview, status_value):
        preview.status = status_value
        preview.save()

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_pending_previews_do_not_count_against_quota(self, mock_delay):
        mock_delay.return_value = None
        for idx in range(2):
            preview = self._create_preview(self.owner, f"pending-{idx}", f"{idx:07x}dead")
            self._set_status(preview, PreviewEnvironment.Status.PENDING)

        resp = self.client.post(
            self.url,
            {"branch_name": "fresh", "commit_sha": "0a1b2c3"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_building_ready_health_check_running_count(self, mock_delay):
        mock_delay.return_value = None
        building = self._create_preview(self.owner, "branch-1", "1111111aaa")
        self._set_status(building, PreviewEnvironment.Status.BUILDING)
        ready = self._create_preview(self.owner, "branch-2", "2222222bbb")
        self._set_status(ready, PreviewEnvironment.Status.READY)
        hcr = self._create_preview(self.owner, "branch-3", "3333333ccc")
        self._set_status(hcr, PreviewEnvironment.Status.HEALTH_CHECK_RUNNING)

        resp = self.client.post(
            self.url,
            {"branch_name": "overflow", "commit_sha": "4444444ddd"},
            format="json",
        )
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Per-user preview quota", str(resp.data))

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_terminal_failure_statuses_do_not_count(self, mock_delay):
        mock_delay.return_value = None
        for idx, status_value in enumerate((
            PreviewEnvironment.Status.BUILD_FAILED,
            PreviewEnvironment.Status.HEALTH_CHECK_FAILED,
            PreviewEnvironment.Status.DESTROYED,
            PreviewEnvironment.Status.EXPIRED,
        )):
            preview = self._create_preview(
                self.owner, f"failed-{idx}", f"55555{idx:02x}fff",
            )
            self._set_status(preview, status_value)

        resp = self.client.post(
            self.url,
            {"branch_name": "fresh", "commit_sha": "0a1b2c3"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_active_count_is_scoped_to_creator(self, mock_delay):
        mock_delay.return_value = None
        other = User.objects.create_user(
            username="f30-other", password="p",
        )
        for idx in range(2):
            preview = self._create_preview(other, f"other-{idx}", f"6{idx:06x}aaa")
            self._set_status(preview, PreviewEnvironment.Status.BUILDING)

        resp = self.client.post(
            self.url,
            {"branch_name": "owner-1", "commit_sha": "0a1b2c3"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
