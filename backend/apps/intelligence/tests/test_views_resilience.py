# pylint: disable=invalid-name
"""Resilience tests for AI intelligence report/anomaly endpoints."""

from unittest.mock import patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from rest_framework.test import APIClient
from django.test import TestCase


User = get_user_model()


class AIResilienceViewTests(TestCase):
    """Ensure AI endpoints degrade gracefully instead of returning 500."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="airesilience",
            email="airesilience@test.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_report_no_data_returns_safe_payload(self):
        response = self.client.get("/api/v1/ai/report/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["available"])
        self.assertEqual(response.data["total_deployments"], 0)

    @patch("apps.intelligence.views.AuditLog.objects.filter")
    def test_report_handles_database_error(self, mock_filter):
        mock_filter.side_effect = DatabaseError("db unavailable")
        response = self.client.get("/api/v1/ai/report/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["available"])
        self.assertIn("storage unavailable", response.data["message"].lower())

    @patch("apps.intelligence.views.AuditLog.objects.filter")
    def test_anomalies_handles_database_error(self, mock_filter):
        mock_filter.side_effect = DatabaseError("db unavailable")
        response = self.client.get("/api/v1/ai/anomalies/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["available"])
        self.assertEqual(response.data["anomalies"], [])

    @patch("apps.intelligence.views.AuditLog.objects.filter")
    def test_report_handles_unserializable_metadata(self, mock_filter):
        qs = mock_filter.return_value
        qs.order_by.return_value.first.return_value = SimpleNamespace(
            metadata={"generated_at": object(), "total_deployments": 3}
        )
        response = self.client.get("/api/v1/ai/report/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["available"])
        self.assertEqual(response.data["total_deployments"], 3)
