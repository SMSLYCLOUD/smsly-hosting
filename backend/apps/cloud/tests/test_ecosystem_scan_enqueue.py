"""Tests for ecosystem-scan enqueue failure handling (no broker needed)."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.ecosystem import EcosystemPlan

URL = "/api/v1/cloud/ecosystem/scan/"
TASK_PATH = "apps.deployments.tasks.ecosystem.ecosystem_scan_task"


class EcosystemScanEnqueueTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="scan-enqueue", password="password123",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_broker_failure_returns_503_and_fails_plan(self):
        """2026-09-15 live incident: .delay() raised OperationalError while
        RabbitMQ restarted -> DRF 500, and the orphaned 'scanning' row
        then 429'd every retry. Must answer 503 with the row failed."""
        with patch(TASK_PATH) as mock_task:
            mock_task.delay.side_effect = Exception(
                "[Errno 111] Connection refused"
            )
            response = self.client.post(URL, {}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "broker_unreachable")
        plan = (
            EcosystemPlan.objects.filter(user=self.user)
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.status, "failed")
        self.assertIn("broker", (plan.error_message or "").lower())

    def test_happy_path_queues_scan(self):
        with patch(TASK_PATH) as mock_task:
            mock_task.delay.return_value = MagicMock(id="task-123")
            response = self.client.post(URL, {}, format="json")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "scanning")
        plan = EcosystemPlan.objects.get(id=body["plan_id"])
        self.assertEqual(plan.status, "scanning")
        self.assertEqual(plan.scan_task_id, "task-123")
