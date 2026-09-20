# pylint: disable=invalid-name
"""Regression tests for the node-provisioning hardening batch.

Covers: TOFU fail-closed store, SSH retry/backoff, rollback key restore,
firewall port parameter, DEGRADED status, agent_ready deploy gate,
api_url validation, lite heartbeat watchdog, serializer contradictions.
"""
import json
import os
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import paramiko
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

User = get_user_model()


class TofuStoreTests(SimpleTestCase):
    def test_corrupt_store_raises_fail_closed(self):
        from apps.deployments.services.ssh_client import _TOFUPolicy
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            fh.write("{not valid json")
            path = fh.name
        try:
            with patch.dict(os.environ, {"SMSLY_KNOWN_HOSTS_PATH": path}):
                policy = _TOFUPolicy("example.com", 22)
                with self.assertRaises(paramiko.SSHException):
                    policy._load_store()
        finally:
            os.unlink(path)

    def test_save_load_roundtrip(self):
        from apps.deployments.services.ssh_client import _TOFUPolicy
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "known_hosts.json")
            with patch.dict(os.environ, {"SMSLY_KNOWN_HOSTS_PATH": path}):
                policy = _TOFUPolicy("example.com", 22)
                policy._save_store({"example.com:22": {"fingerprint": "abc"}})
                self.assertTrue(os.path.isfile(path))
                self.assertEqual(
                    policy._load_store(),
                    {"example.com:22": {"fingerprint": "abc"}},
                )


class SshRetryTests(SimpleTestCase):
    def _server(self, **kwargs):
        defaults = dict(
            host="10.9.9.9", ssh_port=22, ssh_user="root",
            ssh_key="", ssh_password="pw", ssh_key_passphrase="",
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_transient_failure_retried_then_succeeds(self):
        from apps.deployments.services.provisioner.helpers import ssh as ssh_mod
        client = MagicMock()
        client.connect.side_effect = [OSError("blip"), None]
        client.get_transport.return_value = None
        with patch.object(ssh_mod.paramiko, "SSHClient", return_value=client), \
             patch("apps.deployments.services.ssh_client._get_tofu_policy", return_value=MagicMock()), \
             patch.object(ssh_mod.time, "sleep") as mock_sleep:
            result = ssh_mod._get_ssh_client(self._server())
        self.assertIs(result, client)
        self.assertEqual(client.connect.call_count, 2)
        mock_sleep.assert_called_once()

    def test_persistent_failure_raises_after_three(self):
        from apps.deployments.services.provisioner.helpers import ssh as ssh_mod
        client = MagicMock()
        client.connect.side_effect = OSError("down")
        with patch.object(ssh_mod.paramiko, "SSHClient", return_value=client), \
             patch("apps.deployments.services.ssh_client._get_tofu_policy", return_value=MagicMock()), \
             patch.object(ssh_mod.time, "sleep"):
            with self.assertRaises(OSError):
                ssh_mod._get_ssh_client(self._server())
        self.assertEqual(client.connect.call_count, 3)

    def test_no_credentials_raises(self):
        from apps.deployments.services.provisioner.helpers import ssh as ssh_mod
        with patch.object(ssh_mod.paramiko, "SSHClient"), \
             patch("apps.deployments.services.ssh_client._get_tofu_policy", return_value=MagicMock()):
            with self.assertRaises(ValueError):
                ssh_mod._get_ssh_client(self._server(ssh_password=""))


class RollbackKeyTests(TestCase):
    def _server(self, **kwargs):
        user = User.objects.create_user(username="rb-%s" % (abs(hash(str(sorted(kwargs.items())))) % 100000), password="x")
        defaults = dict(name="rb-node", host="10.10.10.10", owner=user)
        defaults.update(kwargs)
        from apps.deployments.models.servers import ManagedServer
        return ManagedServer.objects.create(**defaults)

    def test_rollback_restores_operator_key_backup(self):
        from apps.deployments.services.provisioner.provisioning_resources import (
            _ProvisioningResources,
        )
        server = self._server(
            ssh_key="NEW-KEY",
            provider_metadata={"ssh_key_backup": "ORIGINAL-KEY"},
        )
        resources = _ProvisioningResources(server)
        resources._ssh_key_added = True
        with patch.object(_ProvisioningResources, "_remove_ssh_key", return_value=None):
            resources.rollback()
        server.refresh_from_db()
        self.assertEqual(server.ssh_key, "ORIGINAL-KEY")
        self.assertNotIn("ssh_key_backup", server.provider_metadata or {})

    def test_rollback_blanks_only_when_run_added_key(self):
        from apps.deployments.services.provisioner.provisioning_resources import (
            _ProvisioningResources,
        )
        server = self._server(ssh_key="OPERATOR-KEY")
        resources = _ProvisioningResources(server)
        resources._ssh_key_added = False
        with patch.object(_ProvisioningResources, "_remove_ssh_key", return_value=None):
            resources.rollback()
        server.refresh_from_db()
        self.assertEqual(server.ssh_key, "OPERATOR-KEY")

    def test_remove_firewall_rule_uses_given_port(self):
        from apps.deployments.services.provisioner.provisioning_resources import (
            _ProvisioningResources,
        )
        server = self._server()
        resources = _ProvisioningResources(server)
        with patch(
            "apps.deployments.services.provisioner.provisioning_resources.subprocess.run"
        ) as mock_run:
            resources._remove_firewall_rule("10.10.10.10", "5432")
        dports = [
            call.args[0][call.args[0].index("--dport") + 1]
            for call in mock_run.call_args_list
            if "--dport" in call.args[0]
        ]
        self.assertTrue(dports)
        self.assertTrue(all(p == "5432" for p in dports))


class ServerStatusTests(TestCase):
    def test_degraded_status_is_valid_choice(self):
        from apps.deployments.models.servers import ManagedServer
        self.assertIn(
            "DEGRADED", dict(ManagedServer.Status.choices),
        )
        user = User.objects.create_user(username="degraded-u", password="x")
        server = ManagedServer.objects.create(
            name="degraded-node", host="10.10.10.11", owner=user,
            status=ManagedServer.Status.DEGRADED,
        )
        server.refresh_from_db()
        self.assertEqual(server.status, "DEGRADED")


class AgentReadyGateTests(TestCase):
    def _server(self, **kwargs):
        from apps.deployments.models.servers import ManagedServer
        user = User.objects.create_user(
            username="gate-%s" % (abs(hash(str(sorted(kwargs.items())))) % 100000), password="x")
        defaults = dict(
            name="gate-node", host="10.10.10.12", owner=user,
            status=ManagedServer.Status.ONLINE,
        )
        defaults.update(kwargs)
        return ManagedServer.objects.create(**defaults)

    def _resolve(self, server):
        from apps.deployments.views import _helpers as helpers_mod
        request = SimpleNamespace(
            query_params={}, data={"target_server_id": str(server.id)},
            user=SimpleNamespace(is_superuser=True),
        )
        return helpers_mod._resolve_requested_deploy_target(request, SimpleNamespace(id=server.id))

    def test_lite_not_ready_is_rejected(self):
        server = self._server(is_lite_agent=True, agent_ready=False)
        result = self._resolve(server)
        self.assertFalse(result["ok"])
        self.assertEqual(result["response"].status_code, 400)

    def test_lite_ready_passes(self):
        server = self._server(is_lite_agent=True, agent_ready=True)
        result = self._resolve(server)
        self.assertTrue(result["ok"])

    def test_non_lite_online_passes_without_flag(self):
        server = self._server(is_lite_agent=False, agent_ready=False)
        result = self._resolve(server)
        self.assertTrue(result["ok"])


class ApiUrlValidationTests(SimpleTestCase):
    def _validate(self, value):
        from apps.deployments.views.server.serializers import (
            ManagedServerCreateSerializer,
        )
        serializer = ManagedServerCreateSerializer()
        return serializer.validate_api_url(value)

    def test_mesh_private_ip_allowed(self):
        self.assertEqual(
            self._validate("http://10.100.0.5:8000"),
            "http://10.100.0.5:8000",
        )

    def test_loopback_rejected(self):
        from rest_framework import serializers as drf_serializers
        with self.assertRaises(drf_serializers.ValidationError):
            self._validate("http://127.0.0.1:8000")

    def test_link_local_rejected(self):
        from rest_framework import serializers as drf_serializers
        with self.assertRaises(drf_serializers.ValidationError):
            self._validate("http://169.254.169.254/")

    def test_contradictory_flags_raise(self):
        from apps.deployments.views.server.serializers import (
            ManagedServerProvisionSerializer,
        )
        serializer = ManagedServerProvisionSerializer(data={
            "name": "x", "host": "1.2.3.4", "ssh_password": "x",
            "node_type": "node", "is_lite_agent": True,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("is_lite_agent", serializer.errors)


class LiteWatchdogTests(TestCase):
    def _server(self, **kwargs):
        from apps.deployments.models.servers import ManagedServer
        user = User.objects.create_user(
            username="wd-%s" % (abs(hash(str(sorted(kwargs.items())))) % 100000), password="x")
        defaults = dict(
            name="wd-node", host="10.10.10.13", owner=user,
            is_lite_agent=True, status=ManagedServer.Status.ONLINE,
        )
        defaults.update(kwargs)
        return ManagedServer.objects.create(**defaults)

    def _run_watchdog(self):
        # tasks_health imports these lazily inside the task body, so
        # patch them at their DEFINING modules (the names don't exist
        # on tasks_health until the task runs).
        from apps.deployments.services import self_healing_orchestrator as sho_mod
        with patch.object(
            sho_mod.SelfHealingOrchestrator, "run_full_diagnostics",
            return_value=MagicMock(docker_running=True, network_reachable=True),
        ), patch(
            "apps.autoscaler.services.prometheus_targets.deploy_docker_labels_exporter_on_node",
            return_value=True,
        ), patch(
            "apps.autoscaler.services.prometheus_targets.deploy_promtail_on_node",
            return_value=True,
        ), patch(
            "apps.autoscaler.services.prometheus_targets.deploy_cadvisor_on_node",
            return_value=True,
        ), patch(
            "apps.autoscaler.services.prometheus_targets.deploy_node_exporter_on_node",
            return_value=True,
        ):
            from apps.deployments.tasks.infra.tasks_health import node_watchdog_task
            return node_watchdog_task.run()

    def test_fresh_heartbeat_keeps_online(self):
        server = self._server(last_agent_heartbeat_at=timezone.now())
        self._run_watchdog()
        server.refresh_from_db()
        self.assertEqual(server.status, "ONLINE")

    def test_stale_heartbeat_marks_offline(self):
        server = self._server(
            last_agent_heartbeat_at=timezone.now() - timedelta(minutes=30))
        self._run_watchdog()
        server.refresh_from_db()
        self.assertEqual(server.status, "OFFLINE")
