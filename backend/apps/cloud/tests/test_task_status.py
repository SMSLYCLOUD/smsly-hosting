# pylint: disable=invalid-name
"""Tests for ecosystem task status endpoint resilience."""

from unittest.mock import MagicMock, patch

from celery.exceptions import NotRegistered, SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


User = get_user_model()


class EcosystemTaskStatusTests(TestCase):
    """Ensure task_status never returns non-serializable payloads."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="clouduser",
            email="clouduser@test.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("celery.result.AsyncResult")
    def test_task_status_handles_not_registered_exception(self, mock_async_result):
        result = MagicMock()
        result.ready.return_value = True
        result.status = "FAILURE"
        result.result = NotRegistered("apps.deployments.tasks_ecosystem.some_task")
        mock_async_result.return_value = result

        response = self.client.get(
            "/api/v1/cloud/ecosystem/task_status/",
            {"task_id": "1234"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "FAILURE")
        self.assertEqual(
            response.data["result"]["exception_type"],
            "NotRegistered",
        )

    @patch("celery.result.AsyncResult")
    def test_task_status_promotes_timeout_error(self, mock_async_result):
        result = MagicMock()
        result.ready.return_value = True
        result.status = "FAILURE"
        result.result = SoftTimeLimitExceeded()
        mock_async_result.return_value = result

        response = self.client.get(
            "/api/v1/cloud/ecosystem/task_status/",
            {"task_id": "timeout-task"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "FAILURE")
        self.assertEqual(response.data["result"]["exception_type"], "SoftTimeLimitExceeded")
        self.assertIn("timed out", response.data["error"])
