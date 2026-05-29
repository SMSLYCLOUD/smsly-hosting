import unittest
from unittest.mock import patch, MagicMock

@patch('apps.cloud.adapters.firecracker.os.makedirs')
class TestFirecrackerAdapter(unittest.TestCase):

    @patch('apps.cloud.adapters.firecracker.subprocess.Popen')
    @patch('apps.cloud.adapters.firecracker.subprocess.run')
    @patch('apps.cloud.adapters.firecracker.requests.Session')
    @patch('apps.cloud.adapters.firecracker.os.path.exists')
    @patch('apps.cloud.adapters.firecracker.os.remove')
    @patch('apps.cloud.adapters.firecracker.time.sleep')
    def test_create_instance(self, mock_sleep, mock_remove, mock_exists, mock_session, mock_run, mock_popen, mock_makedirs):
        from apps.cloud.adapters.firecracker import FirecrackerAdapter

        adapter = FirecrackerAdapter()

        # Setup mocks
        mock_exists.return_value = True  # Pretend socket exists to break wait loop

        mock_response = MagicMock()
        mock_response.status_code = 204

        mock_session_instance = MagicMock()
        mock_session_instance.put.return_value = mock_response
        mock_session_instance.get.return_value = mock_response
        mock_session.return_value = mock_session_instance

        # Execute
        instance_id = adapter.create_instance(
            name="test-fvm",
            image="/path/to/rootfs.ext4",
            env={"FOO": "BAR"},
            resources={"cpu": 2, "memory": 1024},
            volumes=[],
            network="smsly-fvm",
            labels={},
            healthcheck={}
        )

        # Assert
        self.assertEqual(instance_id, "test-fvm")
        mock_popen.assert_called_once()
        self.assertTrue(mock_session_instance.put.call_count >= 4) # machine-config, boot-source, drives, etc.

    @patch('apps.cloud.adapters.firecracker.subprocess.check_output')
    @patch('apps.cloud.adapters.firecracker.os.path.exists')
    def test_get_instance_logs(self, mock_exists, mock_check_output, mock_makedirs):
        from apps.cloud.adapters.firecracker import FirecrackerAdapter

        adapter = FirecrackerAdapter()
        mock_exists.return_value = True
        mock_check_output.return_value = b"log line 1\nlog line 2"

        logs = adapter.get_instance_logs("test-fvm", tail=2)

        self.assertEqual(logs, "log line 1\nlog line 2")
