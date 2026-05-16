# pylint: disable=invalid-name
"""API tests for ecosystem scan/deploy compatibility routes."""

from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase


class EcosystemApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="eco-user",
            email="eco-user@example.com",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

    @patch("apps.deployments.tasks_ecosystem.ecosystem_scan_task.delay")
    def test_scan_route_queues_task_with_user(self, delay_mock):
        delay_mock.return_value.id = "scan-task-id"

        response = self.client.post("/api/v1/cloud/ecosystem/scan/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("task_id"), "scan-task-id")

        # Check first argument matches user ID (ignore potential extra args like countdown)
        self.assertTrue(delay_mock.called)
        args, _ = delay_mock.call_args
        self.assertEqual(args[0], str(self.user.id))
        delay_mock.assert_called_once_with(str(self.user.id), 30, ai_provider=None)

    def test_deploy_route_requires_plan_object(self):
        response = self.client.post("/api/v1/cloud/ecosystem/deploy/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("apps.deployments.tasks_ecosystem.ecosystem_deploy_task.delay")
    def test_deploy_route_queues_task_with_plan(self, delay_mock):
        delay_mock.return_value.id = "deploy-task-id"
        plan = {"services": [{"repo": "owner/repo", "name": "repo"}]}

        response = self.client.post(
            "/api/v1/cloud/ecosystem/deploy/",
            {"plan": plan},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("task_id"), "deploy-task-id")
        delay_mock.assert_called_once_with(str(self.user.id), plan)
