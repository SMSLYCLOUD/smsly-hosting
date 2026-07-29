# pylint: disable=invalid-name
"""
Regression tests for Issue 15 (run_command redaction safety).

The ``run_command`` action on a server must NEVER return the
un-redacted output of an SSH command. If the
``_redact_transfer_text`` helper raises, the action must return a
``[REDACTION FAILED — output suppressed for safety]`` placeholder
for both stdout and stderr.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.servers import ManagedServer

User = get_user_model()


class RunCommandRedactionSafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rc-redact", password="123")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="redact-test",
            host="203.0.113.31",
            api_url="https://redact.example.com",
            api_token="tok",
            ssh_user="root",
            ssh_password="ssh-pass",
        )
        self.url = f"/api/v1/servers/{self.server.id}/run_command/"

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    def _exec(self, command="docker ps"):
        with patch(
            "apps.deployments.services.self_healing_orchestrator.SelfHealingOrchestrator"
        ) as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch._exec.return_value = ("", "", 0)
            mock_orch_cls.return_value = mock_orch
            return self.client.post(
                self.url, {"command": command}, format="json"
            )

    def test_redaction_failure_replaces_stdout_with_placeholder(self):
        """If _redact_transfer_text raises for stdout, the response
        must contain the safe placeholder — never the raw output."""
        with patch(
            "apps.deployments.services.self_healing_orchestrator.SelfHealingOrchestrator"
        ) as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch._exec.return_value = (
                "super-secret-token=abc\n",
                "",
                0,
            )
            mock_orch_cls.return_value = mock_orch
            with patch(
                "apps.deployments.views_servers._redact_transfer_text",
                side_effect=RuntimeError("redact blew up"),
            ):
                resp = self.client.post(
                    self.url, {"command": "docker ps"}, format="json"
                )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("super-secret-token", resp.data.get("stdout", ""))
        self.assertIn("REDACTION FAILED", resp.data.get("stdout", ""))

    def test_redaction_failure_replaces_stderr_with_placeholder(self):
        with patch(
            "apps.deployments.services.self_healing_orchestrator.SelfHealingOrchestrator"
        ) as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch._exec.return_value = (
                "",
                "DB_PASSWORD=hunter2 leaked\n",
                0,
            )
            mock_orch_cls.return_value = mock_orch
            with patch(
                "apps.deployments.views_servers._redact_transfer_text",
                side_effect=RuntimeError("redact blew up"),
            ):
                resp = self.client.post(
                    self.url, {"command": "docker ps"}, format="json"
                )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("hunter2", resp.data.get("stderr", ""))
        self.assertIn("REDACTION FAILED", resp.data.get("stderr", ""))

    def test_normal_redaction_still_applies(self):
        """Sanity: when the redaction helper works, secrets are
        still scrubbed from the response (regression guard)."""
        with patch(
            "apps.deployments.services.self_healing_orchestrator.SelfHealingOrchestrator"
        ) as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch._exec.return_value = (
                "API_KEY=hunter2\n",
                "",
                0,
            )
            mock_orch_cls.return_value = mock_orch
            resp = self.client.post(
                self.url, {"command": "docker ps"}, format="json"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("hunter2", resp.data.get("stdout", ""))
        self.assertIn("***", resp.data.get("stdout", ""))
