"""Regression tests for RestartCount reading, child crash-loop detection,
auto-quarantine, and restart-policy recovery.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.core.services import health_monitor as hm
from apps.deployments.models import Deployment, Service
from apps.deployments.models.audit import AuditLog
from apps.deployments.services import power
from apps.deployments.tasks.infra import tasks_container_hygiene as hygiene

User = get_user_model()


class ProbeRestartCountTests(TestCase):
    def test_probe_container_state_reads_top_level_restart_count(self):
        fake_container = SimpleNamespace(
            attrs={
                "RestartCount": 42,
                "State": {
                    "Status": "running",
                    "ExitCode": 0,
                    "StartedAt": "2026-11-28T03:00:00Z",
                    "OOMKilled": False,
                    "Health": {"Status": "healthy", "FailingStreak": 0},
                },
                "Config": {"Labels": {}},
                "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
            }
        )
        fake_client = MagicMock()
        fake_client.containers.get.return_value = fake_container

        with patch("apps.deployments.services.docker_client.get_docker_client", return_value=fake_client):
            info = hm._probe_container_state("cid-top-level")

        self.assertEqual(info["restart_count"], 42)
        self.assertEqual(info["restart_policy"], "unless-stopped")
        self.assertEqual(info["failing_streak"], 0)


class CrashLoopWatchdogQuarantineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="crashloop-owner", email="c@example.com", password="pw",
        )
        self.provider = CloudProvider.objects.create(
            name="local-crash",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    def tearDown(self):
        cache.clear()

    def _make_service(self, name: str, container_id: str, health_status: str = "healthy") -> Service:
        svc = Service.objects.create(
            name=name,
            owner=self.user,
            provider=self.provider,
            status=Service.Status.ACTIVE,
            health_check_path="/health",
            health_status=health_status,
            restart_policy="unless-stopped",
        )
        Deployment.objects.create(
            service=svc,
            commit_hash="abc1234",
            status=Deployment.Status.ACTIVE,
            container_id=container_id,
            created_at=timezone.now() - timezone.timedelta(hours=1),
        )
        return svc

    def test_ignores_healthy_container_with_high_historical_restarts(self):
        svc = self._make_service("smsly-identity-service", "cid111111111111", health_status="healthy")

        def fake_sh(args, timeout=60):
            if args[:2] == ["docker", "ps"]:
                return SimpleNamespace(returncode=0, stdout="cid111111111111\n", stderr="")
            if args[:2] == ["docker", "inspect"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="cid111111111111\t/smsly-identity-service\t60\trunning\t0\thealthy\t0\tunless-stopped\n",
                    stderr="",
                )
            raise AssertionError(f"Unexpected docker command: {args}")

        with (
            patch.object(hygiene, "_sh", side_effect=fake_sh),
            patch("apps.deployments.services.power.subprocess.run") as sub_mock,
        ):
            res = hygiene.container_restart_loop_watchdog_task()

        self.assertEqual(res["findings"], [])
        self.assertEqual(res["quarantined"], [])
        sub_mock.assert_not_called()
        svc.refresh_from_db()
        self.assertEqual(svc.health_status, "healthy")

    def test_quarantines_restarting_tenant_container(self):
        svc = self._make_service("smsly-conversational-ai", "cid222222222222", health_status="unhealthy")

        def fake_sh(args, timeout=60):
            if args[:2] == ["docker", "ps"]:
                return SimpleNamespace(returncode=0, stdout="cid222222222222\n", stderr="")
            if args[:2] == ["docker", "inspect"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="cid222222222222\t/smsly-conversational-ai\t7\trestarting\t1\tunhealthy\t4\tunless-stopped\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(hygiene, "_sh", side_effect=fake_sh),
            patch("apps.core.services.health_monitor._save_container_logs_to_deployment"),
            patch("apps.core.services.health_monitor._dispatch_health_alert"),
            patch("apps.deployments.services.power.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as sub_mock,
        ):
            res = hygiene.container_restart_loop_watchdog_task()

        self.assertEqual(len(res["findings"]), 1)
        self.assertEqual(res["quarantined"], ["smsly-conversational-ai"])
        svc.refresh_from_db()
        self.assertEqual(svc.health_status, "needs_manual_intervention")
        calls = [c.args[0] for c in sub_mock.call_args_list]
        self.assertIn(["docker", "update", "--restart=no", "smsly-conversational-ai"], calls)
        self.assertIn(["docker", "stop", "--timeout", "10", "smsly-conversational-ai"], calls)
        self.assertTrue(
            AuditLog.objects.filter(
                action="CONTAINER_CRASH_LOOP_QUARANTINED",
                target="Service: smsly-conversational-ai",
            ).exists()
        )

    def test_quarantines_child_process_crash_loop_when_restart_count_zero(self):
        svc = self._make_service("smsly-audit-log-service", "cid333333333333", health_status="unhealthy")
        child_logs = "\n".join([
            "INFO:     Waiting for child process [56847]",
            "RuntimeError: FATAL: smsly_core.auth_middleware required in production! Error: No module named 'requests'",
            "INFO:     Child process [56847] died",
            "INFO:     Waiting for child process [56857]",
            "INFO:     Child process [56857] died",
        ])

        def fake_sh(args, timeout=60):
            if args[:2] == ["docker", "ps"]:
                return SimpleNamespace(returncode=0, stdout="cid333333333333\n", stderr="")
            if args[:2] == ["docker", "inspect"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="cid333333333333\t/smsly-audit-log-service\t0\trunning\t0\tunhealthy\t12\tunless-stopped\n",
                    stderr="",
                )
            if args[:2] == ["docker", "logs"]:
                return SimpleNamespace(returncode=0, stdout=child_logs, stderr="")
            raise AssertionError(f"Unexpected docker command: {args}")

        with (
            patch.object(hygiene, "_sh", side_effect=fake_sh),
            patch("apps.core.services.health_monitor._save_container_logs_to_deployment"),
            patch("apps.core.services.health_monitor._dispatch_health_alert"),
            patch("apps.deployments.services.power.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as sub_mock,
        ):
            res = hygiene.container_restart_loop_watchdog_task()

        self.assertEqual(len(res["findings"]), 1)
        self.assertIn("child_crash_loop", res["findings"][0]["reason"])
        self.assertEqual(res["quarantined"], ["smsly-audit-log-service"])
        svc.refresh_from_db()
        self.assertEqual(svc.health_status, "needs_manual_intervention")
        calls = [c.args[0] for c in sub_mock.call_args_list]
        self.assertIn(["docker", "update", "--restart=no", "smsly-audit-log-service"], calls)
        self.assertIn(["docker", "stop", "--timeout", "10", "smsly-audit-log-service"], calls)

    def test_alerts_on_but_never_auto_stops_platform_or_addon_containers(self):
        def fake_sh(args, timeout=60):
            if args[:2] == ["docker", "ps"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="cid444\ncid555\ncid666\n",
                    stderr="",
                )
            if args[:2] == ["docker", "inspect"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "cid444\t/smsly-hosting-backend-1\t8\trestarting\t1\tunhealthy\t5\tunless-stopped\n"
                        "cid555\t/smsly-addon-redis-123\t9\trestarting\t1\tnone\t0\tunless-stopped\n"
                        "cid666\t/smsly-appsec-agent\t6\trestarting\t1\tnone\t0\tunless-stopped\n"
                    ),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(hygiene, "_sh", side_effect=fake_sh),
            patch("apps.deployments.services.power.subprocess.run") as sub_mock,
        ):
            res = hygiene.container_restart_loop_watchdog_task()

        self.assertEqual(len(res["findings"]), 3)
        self.assertEqual(res["quarantined"], [])
        sub_mock.assert_not_called()


class HealthMonitorQuarantineAndRestoreTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="hm-quarantine", email="hmq@example.com", password="pw",
        )
        self.provider = CloudProvider.objects.create(
            name="local-hmq",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="quarantined-app",
            owner=self.user,
            provider=self.provider,
            status=Service.Status.STOPPED,
            health_check_path="/health",
            health_status="needs_manual_intervention",
            restart_policy="unless-stopped",
        )
        self.deploy = Deployment.objects.create(
            service=self.service,
            commit_hash="def5678",
            status=Deployment.Status.ACTIVE,
            container_id="cid-quar-999",
            created_at=timezone.now() - timezone.timedelta(hours=1),
        )

    def tearDown(self):
        cache.clear()

    def test_check_service_health_skips_quarantined_stopped_container(self):
        self.service.status = Service.Status.ACTIVE
        self.service.health_status = "needs_manual_intervention"
        self.service.save(update_fields=["status", "health_status"])

        with (
            patch(
                "apps.core.services.health_monitor._probe_container_state",
                return_value={
                    "status": "exited",
                    "health": "",
                    "exit_code": 1,
                    "restart_count": 7,
                    "started_at": "",
                    "oom_killed": False,
                    "sablier_enrolled": False,
                    "failing_streak": 0,
                    "restart_policy": "no",
                },
            ),
            patch("apps.core.services.health_monitor.requests.get") as req_mock,
            patch("apps.core.services.health_monitor._handle_failure") as fail_mock,
        ):
            hm._check_service_health(self.service, Deployment)

        req_mock.assert_not_called()
        fail_mock.assert_not_called()

    def test_start_and_restart_restore_restart_policy(self):
        fake_runtime = MagicMock()
        with (
            patch("apps.deployments.services.container_runtime.ContainerRuntime", return_value=fake_runtime),
            patch("apps.deployments.services.power.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as sub_mock,
        ):
            start_res = power.start_service(self.service, actor="tester")
            restart_res = power.restart_service(self.service, actor="tester")

        self.assertTrue(start_res["ok"])
        self.assertTrue(restart_res["ok"])
        self.assertEqual(
            sub_mock.call_args_list[0].args[0],
            ["docker", "update", "--restart=unless-stopped", "cid-quar-999"],
        )
        self.assertEqual(
            sub_mock.call_args_list[1].args[0],
            ["docker", "update", "--restart=unless-stopped", "cid-quar-999"],
        )
        fake_runtime.start_container.assert_called_once_with("cid-quar-999")
        fake_runtime.restart_container.assert_called_once_with("cid-quar-999")
