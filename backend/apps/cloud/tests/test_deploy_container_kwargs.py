import unittest
from unittest.mock import MagicMock, patch
from apps.cloud.adapters.local import LocalAdapter

class TestLocalAdapterDeployContainer(unittest.TestCase):
    @patch('apps.cloud.adapters.local.client') # k8s client mock
    @patch('apps.cloud.adapters.local.config') # k8s config mock
    def test_deploy_container_kwargs_no_duplicates(self, mock_k8s_config, mock_k8s_client):
        # Setup mock adapter
        adapter = LocalAdapter(mode='AUTO')
        adapter.k8s_client = None
        adapter.docker_client = MagicMock()
        adapter._deploy_docker = MagicMock(return_value="container_id")

        # Call deploy_container with kwargs that were previously problematic
        adapter.deploy_container(
            service_name="test-service",
            image="test-image",
            env_vars={"ENV": "test"},
            cpu=100,
            memory=128,
            replicas=1,
            vpa_enabled=True,
            volumes=[{"name": "vol", "mount_path": "/data"}],
            healthcheck={"path": "/health"},
            restart_policy="always",
            command="run.sh",
            extra_kwarg="value"
        )

        # Assert _deploy_docker was called correctly without duplicate kwargs in **kwargs
        adapter._deploy_docker.assert_called_once_with(
            "test-service",
            "test-image",
            {"ENV": "test"},
            volumes=[{"name": "vol", "mount_path": "/data"}],
            healthcheck={"path": "/health"},
            cpu=100,
            memory=128,
            restart_policy="always",
            command="run.sh",
            vpa_enabled=True,
            extra_kwarg="value" # Should still be present as an extra kwarg
        )

if __name__ == '__main__':
    unittest.main()
