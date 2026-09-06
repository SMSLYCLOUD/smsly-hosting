"""Regression tests for the Intelligence page data tabs.

Root-cause bugs fixed here:
  1. AuditLog has NO created_at field (it's `timestamp`) — the daily
     report generator crashed on created_at__gte every morning, and the
     report/anomaly readers crashed on order_by('-created_at'), with
     the blanket excepts turning it into "available: false" empty tabs.
  2. The autoscaler status endpoint timed out (>15s) collecting
     per-container docker-py stats serially (~2s x 67 containers); the
     CLI bulk path makes it one ~2s round-trip.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token

from apps.core.models.audit import AuditLog

User = get_user_model()


class IntelligenceAuditLogTimestampTests(TestCase):
    """All Intelligence readers/writers must use AuditLog.timestamp."""

    def setUp(self):
        self.user = User.objects.create_user(username="intel-user", password="x")
        self.token = Token.objects.create(user=self.user)

    def _report_row(self, **meta):
        return AuditLog.objects.create(
            actor="AI_REPORTER",
            action="DAILY_REPORT",
            target="SYSTEM",
            metadata={
                "total_deployments": 3,
                "failed_deployments": 1,
                "success_rate": "66.7%",
                "anomalies_detected": 0,
                "generated_at": "2026-09-05T06:00:00+00:00",
                **meta,
            },
        )

    def test_daily_report_task_writes_row(self):
        from apps.intelligence.tasks import daily_intelligence_report_task
        result = daily_intelligence_report_task.apply().get(timeout=60)
        self.assertTrue(
            AuditLog.objects.filter(actor="AI_REPORTER", action="DAILY_REPORT").exists()
        )

    def test_report_view_returns_data(self):
        self._report_row()
        from django.test import Client
        c = Client(SERVER_NAME="grid.smsly.cloud")
        r = c.get(
            "/api/v1/ai/report/",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("available"), data)
        self.assertEqual(data.get("total_deployments"), 3)

    def test_anomalies_view_returns_rows(self):
        AuditLog.objects.create(
            actor="AI_REMEDIATOR",
            action="SCALE_UP",
            target="my-svc",
            metadata={"severity": "WARNING", "detail": "cpu 95%"},
        )
        from django.test import Client
        c = Client(SERVER_NAME="grid.smsly.cloud")
        r = c.get(
            "/api/v1/ai/anomalies/",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("available"), data)
        self.assertEqual(len(data.get("anomalies", [])), 1)
        anom = data["anomalies"][0]
        self.assertEqual(anom["service_name"], "my-svc")
        self.assertTrue(anom["auto_fixed"])  # SCALE_UP is an action type
        # detected_at must be an ISO string (from AuditLog.timestamp)
        self.assertTrue(anom["detected_at"])

    def test_anomaly_count_in_daily_report(self):
        AuditLog.objects.create(
            actor="AI_REMEDIATOR", action="RESTART", target="svc", metadata={},
        )
        from apps.intelligence.tasks import daily_intelligence_report_task
        daily_intelligence_report_task.apply().get(timeout=60)
        row = (
            AuditLog.objects.filter(actor="AI_REPORTER", action="DAILY_REPORT")
            .order_by("-timestamp").first()
        )
        self.assertEqual(row.metadata.get("anomalies_detected"), 1)


class DockerStatsCliTests(TestCase):
    def test_cli_collector_parses_output(self):
        from unittest import mock
        from apps.autoscaler.engine import container_metrics as cm

        sample = (
            '{"BlockIO":"0B / 0B","CPUPercentage":"2.31%","Container":"abc",'
            '"ID":"abc","MemPerc":"1.20%","MemUsage":"50.2MiB / 512MiB",'
            '"Name":"my-app","NetIO":"1.5kB / 2.5kB","PIDs":"12"}\n'
            '{"BlockIO":"0B / 0B","CPUPercentage":"0.00%","Container":"def",'
            '"ID":"def","MemPerc":"0.50%","MemUsage":"20.0MiB / 256MiB",'
            '"Name":"worker","NetIO":"0B / 0B","PIDs":"3"}\n'
        )
        fake = mock.Mock(returncode=0, stdout=sample, stderr="")
        with mock.patch.object(cm.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(cm.subprocess, "run", return_value=fake):
            result = cm._docker_stats_cli()

        self.assertIsNotNone(result)
        self.assertEqual(result["my-app"]["cpu_percent"], 2.31)
        self.assertEqual(result["my-app"]["memory_mb"], 50.2)
        self.assertEqual(result["my-app"]["memory_limit_mb"], 512.0)
        self.assertEqual(result["my-app"]["pids"], 12)
        self.assertEqual(result["worker"]["cpu_percent"], 0.0)

    def test_cli_collector_none_when_no_docker(self):
        from unittest import mock
        from apps.autoscaler.engine import container_metrics as cm
        with mock.patch.object(cm.shutil, "which", return_value=None):
            self.assertIsNone(cm._docker_stats_cli())

    def test_collect_prefers_cli(self):
        from unittest import mock
        from apps.autoscaler.engine import container_metrics as cm
        with mock.patch.object(cm, "_docker_stats_cli", return_value={"x": {"cpu_percent": 1}}), \
             mock.patch.object(cm, "k8s_available", return_value=False), \
             mock.patch.object(cm, "docker_stats_legacy", side_effect=AssertionError("must not be called")):
            self.assertEqual(cm.collect_container_stats(), {"x": {"cpu_percent": 1}})
