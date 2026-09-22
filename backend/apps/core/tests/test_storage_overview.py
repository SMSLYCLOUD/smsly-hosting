"""Unit tests for PlatformStorageOverviewView (GET and POST /api/v1/system/storage-overview/).

Verifies:
- GET endpoint returns host disk metrics, Docker df breakdown, and platform artifacts stats.
- GET endpoint fails-soft when Docker engine is unreachable or raises an exception.
- POST endpoint enforces staff-only authorization.
- POST endpoint rejects unknown actions with 400 Bad Request.
- POST endpoint dispatches Celery maintenance task for valid actions and returns 202 Accepted.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.views.system import PlatformStorageOverviewView


class PlatformStorageOverviewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = PlatformStorageOverviewView.as_view()
        self.user = User.objects.create_user(username="storage_regular", password="password123")
        self.admin = User.objects.create_superuser(
            username="storage_admin", email="admin@example.com", password="password123"
        )

    def test_anonymous_get_rejected(self):
        request = self.factory.get("/api/v1/system/storage-overview/")
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("shutil.disk_usage", return_value=(100 * (1024 ** 3), 40 * (1024 ** 3), 60 * (1024 ** 3)))
    @patch("docker.from_env")
    def test_authenticated_get_with_docker_metrics(self, mock_docker_env, mock_disk):
        mock_client = MagicMock()
        mock_docker_env.return_value = mock_client
        mock_client.df.return_value = {
            "Images": [
                {"Id": "img1", "Size": 2 * (1024 ** 3), "Containers": 1},
                {"Id": "img2", "Size": 1 * (1024 ** 3), "Containers": 0},  # reclaimable
            ],
            "Containers": [
                {"Id": "c1", "SizeRw": 500 * (1024 ** 2)},
            ],
            "Volumes": [
                {"Name": "vol1", "UsageData": {"Size": 3 * (1024 ** 3), "RefCount": 0}},  # reclaimable
            ],
            "BuildCache": [
                {"ID": "bc1", "Size": 1 * (1024 ** 3), "InUse": False},  # reclaimable
            ],
        }

        request = self.factory.get("/api/v1/system/storage-overview/")
        force_authenticate(request, user=self.user)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data

        # Disk assertions
        self.assertIn("disk", data)
        self.assertEqual(data["disk"]["total_gb"], 100.0)
        self.assertEqual(data["disk"]["used_gb"], 40.0)
        self.assertEqual(data["disk"]["free_gb"], 60.0)
        self.assertEqual(data["disk"]["used_percent"], 40.0)
        self.assertEqual(data["disk"]["status"], "healthy")

        # Docker assertions
        self.assertIn("docker", data)
        self.assertTrue(data["docker"]["available"])
        self.assertEqual(data["docker"]["images"]["count"], 2)
        self.assertEqual(data["docker"]["images"]["size_gb"], 3.0)
        self.assertEqual(data["docker"]["images"]["reclaimable_gb"], 1.0)
        self.assertEqual(data["docker"]["volumes"]["count"], 1)
        self.assertEqual(data["docker"]["volumes"]["size_gb"], 3.0)
        self.assertEqual(data["docker"]["volumes"]["reclaimable_gb"], 3.0)
        self.assertEqual(data["docker"]["build_cache"]["count"], 1)
        self.assertEqual(data["docker"]["build_cache"]["reclaimable_gb"], 1.0)

        # Artifacts assertions
        self.assertIn("artifacts", data)
        self.assertIn("deployments_count", data["artifacts"])
        self.assertIn("active_services", data["artifacts"])

    @patch("docker.from_env", side_effect=Exception("Docker daemon socket not found"))
    def test_authenticated_get_fails_soft_when_docker_offline(self, mock_docker_env):
        request = self.factory.get("/api/v1/system/storage-overview/")
        force_authenticate(request, user=self.user)
        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertFalse(data["docker"]["available"])
        self.assertEqual(data["docker"]["total_docker_gb"], 0.0)

    def test_non_staff_post_forbidden(self):
        request = self.factory.post("/api/v1/system/storage-overview/", {"action": "prune_images"}, format="json")
        force_authenticate(request, user=self.user)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_post_invalid_action(self):
        request = self.factory.post("/api/v1/system/storage-overview/", {"action": "destroy_all"}, format="json")
        force_authenticate(request, user=self.admin)
        response = self.view(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid action", response.data["error"])

    @patch("apps.deployments.tasks.infra.tasks_maintenance.run_maintenance_task.apply_async")
    def test_staff_post_valid_actions(self, mock_apply_async):
        mock_task = MagicMock()
        mock_task.id = "task-uuid-1234"
        mock_apply_async.return_value = mock_task

        actions = [
            ("prune_build_cache", "--clear-build-cache"),
            ("prune_images", "--prune-images"),
            ("registry_gc", "--gc"),
            ("clear_containers", "--clear"),
            ("clean_logs", "--clean-logs"),
            ("docker_recovery", "--docker-recovery"),
        ]

        for action_name, expected_flag in actions:
            request = self.factory.post(
                "/api/v1/system/storage-overview/", {"action": action_name}, format="json"
            )
            force_authenticate(request, user=self.admin)
            response = self.view(request)

            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
            self.assertEqual(response.data["task_id"], "task-uuid-1234")
            self.assertEqual(response.data["status"], "queued")
            self.assertEqual(response.data["action"], action_name)
            mock_apply_async.assert_called_with(kwargs={"command_flag": expected_flag})
