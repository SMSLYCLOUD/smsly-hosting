from unittest.mock import patch, MagicMock
from django.test import TestCase
from apps.deployments.services.transfer_service import _LocalSSHClient


class LocalExecSafetyTests(TestCase):
    @patch('subprocess.run')
    def test_exec_command_uses_shell_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        client = _LocalSSHClient(log_fn=lambda x: None)
        client.exec_command("docker ps -a")
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        self.assertEqual(call_args.kwargs.get('shell'), False)
        self.assertIsInstance(call_args.args[0], list)
        self.assertEqual(call_args.args[0], ['docker', 'ps', '-a'])

    @patch('subprocess.run')
    def test_exec_command_rejects_unsafe_input_via_tokenization(self, mock_run):
        """Even with shell=False, shlex.split prevents semicolon injection."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        client = _LocalSSHClient(log_fn=lambda x: None)
        client.exec_command("ls; rm -rf /")
        call_args = mock_run.call_args
        self.assertEqual(call_args.args[0], ['ls;', 'rm', '-rf', '/'])
