import unittest
from unittest.mock import patch

from apps.cloud.adapters.local import LocalAdapter

class TestPhase2(unittest.TestCase):
    @patch('apps.cloud.adapters.local.LocalAdapter._deploy_firecracker')
    def test_deploy_dispatch_firecracker(self, mock_deploy_fvm):
        adapter = LocalAdapter()
        mock_deploy_fvm.return_value = "fvm-123"

        result = adapter.deploy_container(
            service_name="test-service",
            image="test-image",
            env_vars={},
            cpu=1,
            memory=512,
            runtime="firecracker"
        )

        self.assertEqual(result, "fvm-123")
        mock_deploy_fvm.assert_called_once()
