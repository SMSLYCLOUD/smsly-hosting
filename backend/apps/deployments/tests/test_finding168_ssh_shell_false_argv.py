# pylint: disable=invalid-name
"""Regression tests for Finding #168 (shell=False SSH argv).

Before the fix, ``delete_service_runtime_via_ssh`` invoked
``ssh.exec_command`` with a single shell string like
``"sh -lc '…script…'"``. Paramiko interprets a string argument as
a command line that the remote shell expands, which is the SSH
equivalent of ``subprocess.run(..., shell=True)``. The fix splits
the command with ``shlex.split`` and passes the resulting list to
``exec_command`` so Paramiko invokes ``execvp`` directly with no
intermediate shell evaluation.

These tests verify:
  * The script passed to ``sh -lc`` is preserved verbatim as the
    third element of the argv list (no shell expansion of the
    script body).
  * The argv is a list (not a string).
  * The first three argv elements are ``sh``, ``-lc`` and the
    script itself.
"""

import shlex
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


class Finding168SshShellFalseArgvTests(SimpleTestCase):
    def setUp(self):
        self.server = MagicMock()
        self.server.host = "10.0.0.10"
        self.server.ssh_key = "stub"
        self.server.ssh_password = ""
        self.server.ssh_user = "root"
        self.server.ssh_port = 22
        self.server.wg_address = ""
        self.orchestrator = RemoteOrchestrator(self.server)

    def test_shell_split_produces_list(self):
        """``shlex.split`` of the cleanup command yields a list."""
        script = "echo hello world"
        argv = shlex.split(f"sh -lc {shlex.quote(script)}")
        self.assertIsInstance(argv, list)
        self.assertEqual(len(argv), 3)
        self.assertEqual(argv[0], "sh")
        self.assertEqual(argv[1], "-lc")
        self.assertEqual(argv[2], script)

    def test_argv_is_list_not_string(self):
        """The result of splitting is unambiguously a list, not a str."""
        script = "rm -rf /tmp/something"
        argv = shlex.split(f"sh -lc {shlex.quote(script)}")
        self.assertNotIsInstance(argv, str)
        self.assertIsInstance(argv, list)
        self.assertEqual(len(argv), 3)

    def test_cleanup_invokes_exec_command_with_argv_list(self):
        """The cleanup path passes a list (not a string) to exec_command."""
        captured = {}

        class _FakeSSH:
            def connect(self_inner):
                pass

            def close(self_inner):
                pass

            def exec_command(self_inner, command, timeout=None, raise_on_error=True):
                captured['command'] = command
                captured['timeout'] = timeout
                captured['raise_on_error'] = raise_on_error
                return ("SMSLY_DELETE_NOT_FOUND\n", "", 0)

        service = MagicMock()
        service.name = "fix168-svc"
        service.id = "fix168-id"
        service.active_runtime_id = None
        service.addon_set.all.return_value = []

        with patch(
            "apps.deployments.services.remote_orchestrator.SSHClient",
            return_value=_FakeSSH(),
        ), patch(
            "apps.deployments.services.remote_orchestrator.requests",
        ):
            self.orchestrator.delete_service_runtime_via_ssh(service)

        self.assertIsInstance(captured['command'], list)
        self.assertEqual(captured['command'][0], "sh")
        self.assertEqual(captured['command'][1], "-lc")
        self.assertIn("set +e", captured['command'][2])
        self.assertIn("SMSLY_DELETE", captured['command'][2])
