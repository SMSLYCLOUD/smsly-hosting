import uuid
import base64
import shlex
from unittest.mock import patch, MagicMock
from django.test import TestCase
from apps.deployments.models_mesh import MeshNetwork, WireGuardPeer
from apps.deployments.services.wireguard_service import WireGuardService
from apps.deployments.models_servers import ManagedServer
import docker

class MeshNetworkTest(TestCase):
    def test_next_available_ip(self):
        mesh = MeshNetwork.objects.create(name="test", subnet="10.100.0.0/24")
        self.assertEqual(mesh.next_available_ip(), "10.100.0.1")

        WireGuardPeer.objects.create(mesh=mesh, wg_address="10.100.0.1", public_key="test1", private_key="test1")
        self.assertEqual(mesh.next_available_ip(), "10.100.0.2")

        WireGuardPeer.objects.create(mesh=mesh, wg_address="10.100.0.2", public_key="test2", private_key="test2")
        self.assertEqual(mesh.next_available_ip(), "10.100.0.3")

    @patch('docker.from_env')
    def test_deploy_local_validates_and_encodes_config(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        config = "[Interface]\nPrivateKey = test\nAddress = 10.100.0.1/24\n"

        WireGuardService._deploy_local(config, "wg0")

        # Check that the first docker run uses the safe interface and base64 config
        b64_config = base64.b64encode(config.encode()).decode()
        expected_cmd = f"mkdir -p /etc/wireguard && echo '{b64_config}' | base64 -d > /etc/wireguard/wg0.conf && chmod 600 /etc/wireguard/wg0.conf"

        mock_client.containers.run.assert_any_call(
            "alpine",
            command=["sh", "-c", expected_cmd],
            remove=True,
            environment={"DOCKER_HOST": "tcp://socket-proxy:2375"},
            volumes={"/etc/wireguard": {"bind": "/etc/wireguard", "mode": "rw"}},
        )

    @patch('docker.from_env')
    def test_deploy_local_rejects_unsafe_interface(self, mock_docker):
        config = "[Interface]\nPrivateKey = test\nAddress = 10.100.0.1/24\n"
        with self.assertRaises(ValueError):
            WireGuardService._deploy_local(config, "wg0; rm -rf /")
        mock_docker.assert_not_called()

    @patch('apps.deployments.services.wireguard_service.WireGuardService._ssh_run')
    def test_deploy_remote_validates_and_encodes_config(self, mock_ssh):
        config = "[Interface]\nPrivateKey = test\nAddress = 10.100.0.2/24\n"

        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(username="test", password="test")
        server = ManagedServer.objects.create(name="test-server", host="1.1.1.1", owner=user)

        WireGuardService._deploy_remote(server, config, "wg0")

        b64_config = base64.b64encode(config.encode()).decode()
        mock_ssh.assert_called_once()
        called_server, called_command = mock_ssh.call_args.args[:2]
        self.assertEqual(called_server, server)
        self.assertIn("sudo -n", called_command)
        self.assertIn(shlex.quote(b64_config), called_command)
        self.assertIn("wg-quick up wg0", called_command)
        self.assertIn("wg show wg0", called_command)

    @patch('apps.deployments.services.wireguard_service.WireGuardService._ssh_run')
    def test_deploy_remote_rejects_incomplete_config(self, mock_ssh):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(username="test2", password="test")
        server = ManagedServer.objects.create(name="test-server-2", host="1.1.1.2", owner=user)

        with self.assertRaises(ValueError):
            WireGuardService._deploy_remote(server, "[Interface]\nPrivateKey = test\n", "wg0")

        mock_ssh.assert_not_called()
