"""Node Caddy hash-converged fanout tests."""
import hashlib
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.core import ManagedServer

User = get_user_model()


def _node(user, name="caddy-node-1", status="ONLINE"):
    return ManagedServer.objects.create(
        name=name, host="10.9.9.9", is_primary=False, is_lite_agent=False,
        status=status, owner=user, ssh_key="dummy",
    )


class PushCaddyHashTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="caddy-user", password="x")

    def _push(self, content="line1\n", reload_code=0):
        from apps.deployments.tasks.deploy import caddy as caddy_tasks
        server = _node(self.user)
        with patch(
            "apps.deployments.services.caddy_manager.config_generation.generate_node_caddyfile",
            return_value=content,
        ), patch(
            "apps.deployments.services.ssh_client.SSHClient",
        ) as mock_ssh_cls:
            ssh = MagicMock()
            ssh.exec_command.side_effect = [("", "", 0), ("", "", reload_code)]
            mock_ssh_cls.return_value = ssh
            result = caddy_tasks.push_caddy_to_node(str(server.id))
        server.refresh_from_db()
        return result, server

    def test_success_stamps_hash(self):
        result, server = self._push()
        self.assertTrue(result["ok"])
        want = hashlib.sha256(b"line1\n").hexdigest()
        self.assertEqual(server.provider_metadata.get("caddy_config_sha256"), want)

    def test_failure_stamps_nothing(self):
        result, server = self._push(reload_code=1)
        self.assertFalse(result["ok"])
        self.assertNotIn("caddy_config_sha256", server.provider_metadata)


class ReconcileCaddyTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="caddy-rec-user", password="x")

    def _run(self):
        from apps.deployments.tasks.deploy import caddy as caddy_tasks
        return caddy_tasks.reconcile_node_caddyfiles_task()

    def test_converged_skips_push(self):
        content = "same\n"
        node = _node(self.user)
        node.provider_metadata = {
            "caddy_config_sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
        node.save(update_fields=["provider_metadata"])
        with patch(
            "apps.deployments.services.caddy_manager.config_generation.generate_node_caddyfile",
            return_value=content,
        ), patch(
            "apps.deployments.tasks.deploy.caddy.push_caddy_to_node",
        ) as mock_push:
            report = self._run()
        mock_push.assert_not_called()
        self.assertIn(node.name, report["converged"])

    def test_drift_pushes(self):
        node = _node(self.user, name="caddy-node-drift")
        with patch(
            "apps.deployments.services.caddy_manager.config_generation.generate_node_caddyfile",
            return_value="new content\n",
        ), patch(
            "apps.deployments.tasks.deploy.caddy.push_caddy_to_node",
            return_value={"ok": True},
        ) as mock_push:
            report = self._run()
        mock_push.assert_called_once_with(str(node.id))
        self.assertIn(node.name, report["pushed"])

    def test_offline_and_empty_skipped(self):
        _node(self.user, name="caddy-node-off", status="OFFLINE")
        _node(self.user, name="caddy-node-empty")
        with patch(
            "apps.deployments.services.caddy_manager.config_generation.generate_node_caddyfile",
            return_value="",
        ), patch(
            "apps.deployments.tasks.deploy.caddy.push_caddy_to_node",
        ) as mock_push:
            report = self._run()
        mock_push.assert_not_called()
        self.assertEqual(len(report["skipped"]), 2)

    def test_push_failure_reported_not_raised(self):
        node = _node(self.user, name="caddy-node-fail")
        with patch(
            "apps.deployments.services.caddy_manager.config_generation.generate_node_caddyfile",
            return_value="x\n",
        ), patch(
            "apps.deployments.tasks.deploy.caddy.push_caddy_to_node",
            return_value={"ok": False, "message": "ssh down"},
        ):
            report = self._run()
        self.assertEqual(report["status"], "ok")
        self.assertTrue(any(node.name in f for f in report["failed"]))
