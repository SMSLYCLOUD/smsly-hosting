# pylint: disable=invalid-name
"""Regression tests for the Alert & Monitoring System review fixes (P0/P1/P2)."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.cloud.services.ssl_monitor import SSLMonitorService
from apps.core.services import health_monitor as hm
from apps.core.services.log_watchdog import watch_log_errors_task
from apps.core.tasks.metrics import _check_metric_thresholds
from apps.deployments.models import AuditLog, Deployment, Service
from apps.notifications.models import ResourceAlert
from apps.notifications.tasks import (
    _dispatch_webhook,
    _dispatch_whatsapp,
    _rule_metric_value,
    _safe_audit_metadata,
    dispatch_notification,
)
from config.celery import app as celery_app

User = get_user_model()


class AlertAndMonitoringRegressionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="alert-owner",
            email="owner@example.com",
            password="password123",
        )
        self.other = User.objects.create_user(
            username="alert-other",
            email="other@example.com",
            password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="local-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="api",
            owner=self.owner,
            provider=self.provider,
            health_check_path="/health",
            memory_mb=1024,
        )
        self.other_service = Service.objects.create(
            name="payment-api",
            owner=self.other,
            provider=self.provider,
        )

    def tearDown(self):
        cache.clear()

    def test_c2_whatsapp_webhook_blocks_internal_ssrf(self):
        """_dispatch_whatsapp must reject loopback/metadata webhook URLs."""
        with patch("apps.notifications.tasks.requests.post") as mock_post:
            res = _dispatch_whatsapp(
                self.owner,
                "Alert body",
                {"whatsapp_webhook_url": "http://169.254.169.254/latest/meta-data/"},
            )
        self.assertEqual(res["status"], "failed")
        mock_post.assert_not_called()

    def test_c3_audit_metadata_redacts_secrets_and_webhooks(self):
        """dispatch_notification must never write raw tokens or webhook URLs into AuditLog."""
        raw_meta = {
            "service": "api",
            "telegram_bot_token": "123456:ABC-DEF-SECRET-TOKEN",
            "twilio_auth_token": "twilio-super-secret-token",
            "whatsapp_webhook_url": "https://example.com/secret-hook",
            "slack_webhook_url": "https://hooks.slack.com/services/T/B/SECRET",
        }
        safe = _safe_audit_metadata(raw_meta)
        self.assertEqual(safe["service"], "api")
        self.assertEqual(safe["telegram_bot_token"], "[REDACTED]")
        self.assertEqual(safe["twilio_auth_token"], "[REDACTED]")
        self.assertEqual(safe["whatsapp_webhook_url"], "[REDACTED]")
        self.assertEqual(safe["slack_webhook_url"], "[REDACTED]")

        dispatch_notification(
            event_type="deploy_failed",
            user_id=self.owner.id,
            title="Deploy Failed",
            message="Body",
            metadata=raw_meta,
            channels=["in_app"],
        )
        audits = AuditLog.objects.filter(target=f"user:{self.owner.id}")
        self.assertTrue(audits.exists())
        for audit in audits:
            dump = str(audit.metadata)
            self.assertNotIn("123456:ABC-DEF-SECRET-TOKEN", dump)
            self.assertNotIn("twilio-super-secret-token", dump)
            self.assertNotIn("https://hooks.slack.com/services/T/B/SECRET", dump)

    def test_c4_remote_cadvisor_and_node_exporter_skip_when_running_and_drop_privileged(self):
        """deploy_cadvisor_on_node and deploy_node_exporter_on_node must skip recreate when already running."""
        from apps.autoscaler.services.prometheus_targets import (
            deploy_cadvisor_on_node,
            deploy_node_exporter_on_node,
        )

        server = MagicMock()
        server.name = "node-1"
        server.host = "203.0.113.10"
        server.wg_address = "10.100.0.2"
        server.ssh_key = "key"
        server.ssh_user = "root"
        server.ssh_port = 22
        server.ssh_password = ""

        with patch("apps.deployments.services.ssh_client.SSHClient") as mock_ssh_cls:
            client = MagicMock()
            client.exec_command.return_value = ("running\n", "", 0)
            mock_ssh_cls.return_value = client

            self.assertTrue(deploy_cadvisor_on_node(server, force=False))
            self.assertEqual(client.exec_command.call_count, 1)
            self.assertIn("docker inspect smsly-cadvisor", client.exec_command.call_args[0][0])

            client.exec_command.reset_mock()
            client.exec_command.return_value = ("running\n", "", 0)
            self.assertTrue(deploy_node_exporter_on_node(server, force=False))
            self.assertEqual(client.exec_command.call_count, 1)
            self.assertIn("docker inspect smsly-node-exporter", client.exec_command.call_args[0][0])

    def test_c5_incident_report_handles_resource_alert_and_isolates_tenant_audits(self):
        """GET /incident-report/ must not crash on ResourceAlert and must not leak other services' logs."""
        ResourceAlert.objects.create(
            service=self.service,
            severity=ResourceAlert.Severity.WARNING,
            metric="cpu",
            threshold=85.0,
            current_value=94.2,
            message="CPU at 94%",
        )
        AuditLog.objects.create(
            actor="system",
            action="SERVICE_UNHEALTHY",
            target=f"Service: {self.service.name}",
            metadata={"service_id": str(self.service.id), "reason": "HTTP 502"},
        )
        AuditLog.objects.create(
            actor="system",
            action="SERVICE_RESTART",
            target=f"Service: {self.other_service.name}",
            metadata={"message": "Other tenant restart"},
        )
        AuditLog.objects.create(
            actor="system",
            action="CADDY_RELOAD",
            target="platform",
            metadata={"message": "Global caddy reload"},
        )

        client = APIClient()
        client.force_authenticate(user=self.owner)
        resp = client.get(f"/api/v1/services/{self.service.id}/incident-report/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        events = resp.data["events"]
        types = [e["type"] for e in events]
        self.assertIn("resource_alert", types)
        self.assertIn("health", types)
        self.assertNotIn("infrastructure", types)
        details = " ".join(str(e.get("detail", "")) for e in events)
        self.assertNotIn("Other tenant restart", details)

    def test_h4_escalation_to_needs_manual_intervention_is_not_dropped_by_unhealthy_cooldown(self):
        """An immediate escalation from 'unhealthy' to 'needs_manual_intervention' must dispatch both alerts."""
        Deployment.objects.create(
            service=self.service,
            commit_hash="abc1234",
            status=Deployment.Status.ACTIVE,
        )
        with patch("apps.core.tasks.alerts.alert_user_task.delay") as mock_alert, \
                patch("apps.core.services.health_monitor._fetch_container_logs", return_value="crash"):
            hm._dispatch_health_alert(self.service, "unhealthy", "probe failed")
            hm._dispatch_health_alert(self.service, "unhealthy", "probe failed again")
            hm._dispatch_health_alert(self.service, "needs_manual_intervention", "auto-rollback triggered")

        self.assertEqual(mock_alert.call_count, 2)

    def test_h5_health_monitor_does_not_clear_active_logwatch_manual_intervention(self):
        """monitor_health_task must not flip a logwatch-flagged service back to 'healthy' while flagged."""
        Deployment.objects.create(
            service=self.service,
            commit_hash="abc1234",
            status=Deployment.Status.ACTIVE,
            container_id="cid123",
        )
        error_burst = "\n".join(f"[ERROR] failure {i}" for i in range(12))
        cache.set(f"logwatch:bad:{self.service.id}", 2, timeout=3600)

        with patch("apps.core.services.health_monitor._fetch_container_logs", return_value=error_burst), \
                patch("apps.core.services.health_monitor._dispatch_health_alert"):
            watch_log_errors_task()

        self.service.refresh_from_db()
        self.assertEqual(self.service.health_status, "needs_manual_intervention")
        self.assertTrue(cache.get(f"logwatch:flagged:{self.service.id}"))

    def test_h6_container_hygiene_tasks_routed_to_deploy_queue(self):
        """All Docker-heavy container hygiene beat tasks must route to the 'deploy' queue."""
        routes = celery_app.conf.task_routes
        for task_name in (
            "apps.deployments.tasks.container_restart_loop_watchdog_task",
            "apps.deployments.tasks.reap_failed_greens_task",
            "apps.deployments.tasks.orphan_addon_gc_task",
            "apps.deployments.tasks.reconcile_gvisor_hosts_task",
        ):
            self.assertEqual(routes.get(task_name), {"queue": "deploy"}, task_name)

    def test_h7_slack_webhook_failure_marks_channel_failed(self):
        """When send_slack_notification returns False, _dispatch_webhook must return status='failed'."""
        with patch("apps.notifications.webhooks.send_slack_notification", return_value=False):
            res = _dispatch_webhook(
                self.owner,
                "Title",
                "Body",
                {},
                webhook_url="https://hooks.slack.com/services/T/B/X",
            )
        self.assertEqual(res["status"], "failed")

    def test_m2_check_metric_thresholds_uses_consistent_percentage_units(self):
        """_check_metric_thresholds must report percentage current_value against percentage threshold."""
        stats = {
            "cpu_usage": 0.2,
            "cpu_limit": 2.0,      # 10% CPU
            "memory_usage": 950.0,
            "memory_limit": 1000.0,  # 95% Memory
        }
        with patch("apps.notifications.tasks.notify_health_alert.delay") as mock_notify:
            _check_metric_thresholds(self.service, stats, timezone.now())

        mock_notify.assert_called_once()
        kwargs = mock_notify.call_args.kwargs
        self.assertEqual(kwargs["current_value"], 95.0)
        self.assertEqual(kwargs["threshold"], 90.0)

    def test_m5_ssl_monitor_resets_fail_count_on_success(self):
        """A successful TLS check must reset ssl_fail_count to 0 and update checked_at."""
        domain_obj = MagicMock()
        domain_obj.domain_name = "app.example.com"
        domain_obj.service.owner = self.owner
        domain_obj.ssl_fail_count = 2
        domain_obj.checked_at = None

        future_str = (timezone.now() + timezone.timedelta(days=45)).strftime("%b %d %H:%M:%S %Y GMT")
        fake_sock = MagicMock()
        fake_sock.__enter__.return_value = fake_sock
        fake_sock.getpeercert.return_value = {"notAfter": future_str}

        with patch("apps.cloud.services.ssl_monitor._is_safe_outbound_target", return_value=True), \
                patch("ssl.create_default_context") as mock_ctx:
            mock_ctx.return_value.wrap_socket.return_value = fake_sock
            SSLMonitorService()._check_cert_domain_obj(domain_obj)

        self.assertEqual(domain_obj.ssl_fail_count, 0)
        self.assertIsNotNone(domain_obj.checked_at)
        self.assertTrue(domain_obj.ssl_active)

    def test_m6_rule_metric_value_supports_status_and_disk(self):
        """_rule_metric_value must evaluate 'status' and 'disk' alert rule types."""
        self.service.health_status = "healthy"
        self.assertEqual(_rule_metric_value(None, "status", service=self.service), 1.0)
        self.service.health_status = "unhealthy"
        self.assertEqual(_rule_metric_value(None, "status", service=self.service), 0.0)

        metric_row = MagicMock(disk_read_bytes=5 * 1024 * 1024, disk_write_bytes=3 * 1024 * 1024)
        self.assertEqual(_rule_metric_value(metric_row, "disk", service=self.service), 8.0)

    def test_g1_metric_thresholds_persist_resource_alert_and_respect_warning_vs_critical(self):
        """_check_metric_thresholds must create ResourceAlert rows and distinguish warning from critical."""
        self.service.alert_config = {"cpu_warning": 65, "cpu_critical": 88}
        self.service.save(update_fields=["alert_config"])
        stats = {
            "cpu_usage": 1.4,
            "cpu_limit": 2.0,      # 70% CPU -> breaches cpu_warning (65), below cpu_critical (88)
            "memory_usage": 256.0,
            "memory_limit": 1024.0,
        }
        with patch("apps.notifications.tasks.notify_health_alert.delay") as mock_notify:
            _check_metric_thresholds(self.service, stats, timezone.now())

        mock_notify.assert_called_once()
        self.assertEqual(mock_notify.call_args.kwargs["current_value"], 70.0)
        self.assertEqual(mock_notify.call_args.kwargs["threshold"], 65.0)
        self.assertEqual(mock_notify.call_args.kwargs["severity"], "WARNING")
        alert_row = ResourceAlert.objects.filter(service=self.service, metric="cpu").first()
        self.assertIsNotNone(alert_row)
        self.assertEqual(alert_row.severity, "WARNING")
        self.assertEqual(alert_row.threshold, 65.0)

    def test_g2_status_alert_rule_evaluates_down_service_with_no_recent_metrics(self):
        """evaluate_alert_rules_task must evaluate 'status' rules even when ServiceMetric is empty."""
        from apps.notifications.models import AlertRule
        from apps.notifications.tasks import evaluate_alert_rules_task

        self.service.health_status = "unhealthy"
        self.service.save(update_fields=["health_status"])
        AlertRule.objects.create(
            name="Service Unhealthy Rule",
            enabled=True,
            metric="status",
            operator="==",
            threshold=0.0,
            severity="critical",
        )
        with patch("apps.notifications.tasks.dispatch_notification.delay") as mock_dispatch:
            outcome = evaluate_alert_rules_task()

        self.assertGreaterEqual(outcome["fired"], 1)
        self.assertTrue(mock_dispatch.called)

    def test_g3_addon_alerts_flag_offline_status(self):
        """check_alerts must emit a critical alert when an addon's status is OFFLINE."""
        from apps.addons.services.alerts import check_alerts

        addon = MagicMock()
        addon.name = "pg-main"
        alerts = check_alerts(addon, {"status": "OFFLINE", "error": "connection refused"})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "critical")
        self.assertIn("OFFLINE", alerts[0]["message"])

    def test_g5_remediator_cooldown_blocks_back_to_back_completed_auto_deploys(self):
        """_has_recent_auto_deployment must return True even after the auto-remediation deploy completes."""
        from apps.intelligence.remediator import RemediationEngine

        Deployment.objects.create(
            service=self.service,
            commit_hash="abc1234",
            status=Deployment.Status.ACTIVE,
            commit_message="Auto-Remediation: Restarting due to health check failure",
        )
        engine = RemediationEngine()
        self.assertTrue(engine._has_recent_auto_deployment(self.service, "Auto-Remediation:"))

    def test_g6_traffic_offset_path_is_deterministic(self):
        """_offset_file_for_path must use deterministic SHA-256 rather than randomized hash()."""
        from pathlib import Path
        from apps.core.tasks.traffic import _offset_file_for_path

        p = Path("/var/log/caddy/access.log")
        self.assertEqual(_offset_file_for_path(p), _offset_file_for_path(p))
        self.assertRegex(_offset_file_for_path(p).name, r"^\.traffic_log_offset_[0-9a-f]{16}$")

