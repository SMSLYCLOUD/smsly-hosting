"""Tests for intelligence runtime anomaly task."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.deployments.models import Deployment, Service
from apps.intelligence.tasks import detect_anomalies_task


class IntelligenceRuntimeTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="runtime-ai-user",
            email="runtime-ai-user@example.com",
            password="password123",
        )
        self.service = Service.objects.create(
            name="runtime-ai-service",
            owner=self.user,
            health_status="healthy",
        )

    @patch("apps.intelligence.tasks.RemediationEngine.apply_fix", return_value=True)
    def test_detect_anomalies_uses_latest_deployment_logs(self, apply_fix_mock):
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash="abc1234",
            build_logs="Out of memory: Kill process",
        )

        summary = detect_anomalies_task(batch_size=20)

        self.assertGreaterEqual(summary.get("checked", 0), 1)
        self.assertGreaterEqual(summary.get("issues_detected", 0), 1)
        apply_fix_mock.assert_called()

    @patch("apps.intelligence.tasks.RemediationEngine.apply_fix", return_value=True)
    def test_unhealthy_service_without_logs_still_triggers_crash_loop_check(self, apply_fix_mock):
        self.service.health_status = "unhealthy"
        self.service.save(update_fields=["health_status"])

        summary = detect_anomalies_task(batch_size=20)

        self.assertGreaterEqual(summary.get("issues_detected", 0), 1)
        apply_fix_mock.assert_called_with("CRASH_LOOP", str(self.service.id))
