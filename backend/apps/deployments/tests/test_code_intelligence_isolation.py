# pylint: disable=invalid-name
"""Tests for cross-tenant isolation in CodeIntelligenceView."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Service

User = get_user_model()


class CodeIntelligenceIsolationTests(TestCase):
    """Ensure repos_data submitted to /api/v1/cloud/ecosystem/deep_scan/
    cannot include repos belonging to other users.
    """

    URL = "/api/v1/cloud/ecosystem/deep_scan/"

    def setUp(self):
        self.user_a = User.objects.create_user(
            username="user-a", email="a@example.com", password="pwd"
        )
        self.user_b = User.objects.create_user(
            username="user-b", email="b@example.com", password="pwd"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user_a)

        self.user_a_service = Service.objects.create(
            name="user-a-service",
            owner=self.user_a,
            repository_url="https://github.com/user-a/repo",
        )
        self.user_b_service = Service.objects.create(
            name="user-b-service",
            owner=self.user_b,
            repository_url="https://github.com/user-b/repo",
        )

    @patch("apps.deployments.tasks_code_intelligence.deep_scan_and_verify_task.delay")
    def test_user_b_repo_in_repos_data_returns_403(self, mock_delay):
        payload = {
            "repos_data": [
                {
                    "id": str(self.user_b_service.id),
                    "owner_id": self.user_b.id,
                    "repo": "user-b/repo",
                }
            ],
            "deploy_plan": {"services": []},
        }
        response = self.client.post(self.URL, payload, format="json")
        self.assertEqual(response.status_code, 403)
        mock_delay.assert_not_called()

    @patch("apps.deployments.tasks_code_intelligence.deep_scan_and_verify_task.delay")
    def test_own_repos_returns_200_and_dispatches_task(self, mock_delay):
        mock_delay.return_value.id = "task-123"
        payload = {
            "repos_data": [
                {
                    "id": str(self.user_a_service.id),
                    "owner_id": self.user_a.id,
                    "repo": "user-a/repo",
                }
            ],
            "deploy_plan": {"services": []},
        }
        response = self.client.post(self.URL, payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["task_id"], "task-123")
        mock_delay.assert_called_once()

    @patch("apps.deployments.tasks_code_intelligence.deep_scan_and_verify_task.delay")
    def test_empty_repos_data_returns_200(self, mock_delay):
        mock_delay.return_value.id = "task-empty"
        payload = {
            "repos_data": [],
            "deploy_plan": {"services": []},
        }
        response = self.client.post(self.URL, payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["task_id"], "task-empty")
        mock_delay.assert_called_once()
