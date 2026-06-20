import contextlib
from unittest.mock import patch

from django.test import TestCase

from apps.deployments.services.safedeploy.command_executor import CommandExecutor


class CommandExecutorTest(TestCase):
    @patch('subprocess.run')
    def test_command_executor_avoids_shell_injection(self, mock_run):
        executor = CommandExecutor()

        # A seemingly innocent command that an attacker might try to inject
        malicious_cmd = "python manage.py check; echo 'pwned'"

        # When shell=False and shlex is used, the semi-colon and second command are treated as arguments to python.
        with contextlib.suppress(Exception):
            executor.run(malicious_cmd, cwd="/tmp")

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args

        # Ensure it was parsed as a list safely and shell=False
        cmd_list = args[0]
        self.assertEqual(cmd_list, ["python", "manage.py", "check;", "echo", "pwned"])
        self.assertFalse(kwargs.get("shell"))
