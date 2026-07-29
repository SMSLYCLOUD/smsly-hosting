"""
Regression tests for Finding #113 (cross-tenant preview spam).

The ``PreviewEnvironmentViewSet.create`` action must reject a
request when the same (service, branch_name, commit_sha) tuple
already has an active preview from the same creator. Without this
check a tenant can spam the queue with duplicate builds to waste
quota, mask failed builds, or otherwise fish for cross-tenant
preview slots.

The branch_name and commit_sha are user-controlled, and the
creator binds them to the request user.  Duplicate-detection is
the minimum bar: an integration with the user's repository would
be tighter, but uniqueness of the active tuple stops the spam.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.core import Service
from apps.deployments.models.safedeploy import PreviewEnvironment

User = get_user_model()


class Finding113CrossTenantPreviewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="cross-tenant-owner-113", password="p",
        )
        self.service = Service.objects.create(
            name="cross-tenant-svc-113",
            owner=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = f"/api/v1/services/{self.service.id}/previews/"

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_duplicate_active_preview_returns_409(self, _delay):
        PreviewEnvironment.objects.create(
            service=self.service,
            branch_name="main",
            commit_sha="abc1234",
            created_by=self.owner,
            status=PreviewEnvironment.Status.READY,
        )
        resp = self.client.post(
            self.url,
            {"branch_name": "main", "commit_sha": "abc1234"},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("already exists", str(resp.data).lower())

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_destroyed_preview_does_not_block_new_one(self, _delay):
        PreviewEnvironment.objects.create(
            service=self.service,
            branch_name="main",
            commit_sha="abc1234",
            created_by=self.owner,
            status=PreviewEnvironment.Status.DESTROYED,
        )
        resp = self.client.post(
            self.url,
            {"branch_name": "main", "commit_sha": "abc1234"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_build_failed_preview_does_not_block_new_one(self, _delay):
        """A failed build for the same tuple should be replaceable."""
        PreviewEnvironment.objects.create(
            service=self.service,
            branch_name="main",
            commit_sha="abc1234",
            created_by=self.owner,
            status=PreviewEnvironment.Status.BUILD_FAILED,
        )
        resp = self.client.post(
            self.url,
            {"branch_name": "main", "commit_sha": "abc1234"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)

    @patch("apps.deployments.tasks_safedeploy.create_preview_environment_job.delay")
    def test_unique_branch_commit_pair_succeeds(self, _delay):
        resp = self.client.post(
            self.url,
            {"branch_name": "feature-x", "commit_sha": "deadbeef"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
