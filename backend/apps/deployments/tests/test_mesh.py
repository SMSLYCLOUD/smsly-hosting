import uuid
import shlex
import base64
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
    def test_deploy_local_sanitizes_input(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        # Deploy config with an interface that could cause shell injection
        malicious_iface = "wg0; rm -rf /"
        safe_iface = shlex.quote(malicious_iface)
        config = "[Interface]\nPrivateKey=test"

        WireGuardService._deploy_local(config, malicious_iface)

        # Check that the first docker run uses the safe interface and base64 config
        b64_config = base64.b64encode(config.encode()).decode()
        expected_cmd = f"mkdir -p /etc/wireguard && echo '{b64_config}' | base64 -d > /etc/wireguard/{safe_iface}.conf && chmod 600 /etc/wireguard/{safe_iface}.conf"

        mock_client.containers.run.assert_any_call(
            "alpine",
            command=["sh", "-c", expected_cmd],
            remove=True,
            environment={"DOCKER_HOST": "tcp://socket-proxy:2375"},
            volumes={"/etc/wireguard": {"bind": "/etc/wireguard", "mode": "rw"}},
        )

    @patch('apps.deployments.services.wireguard_service.WireGuardService._ssh_run')
    def test_deploy_remote_sanitizes_input(self, mock_ssh):
        malicious_iface = "wg0; echo hi"
        safe_iface = shlex.quote(malicious_iface)
        config = "[Interface]\nPrivateKey=test"

        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(username="test", password="test")
        server = ManagedServer.objects.create(name="test-server", host="1.1.1.1", owner=user)

        WireGuardService._deploy_remote(server, config, malicious_iface)

        b64_config = base64.b64encode(config.encode()).decode()
        expected_command = " && ".join([
            "apt-get update > /dev/null 2>&1 || true",
            "apt-get install -y wireguard iptables > /dev/null 2>&1 || true",
            "mkdir -p /etc/wireguard",
            f"echo '{b64_config}' | base64 -d > /etc/wireguard/{safe_iface}.conf",
            f"chmod 600 /etc/wireguard/{safe_iface}.conf",
            "modprobe wireguard || true",
            f"wg-quick down {safe_iface} 2>/dev/null || true",
            f"wg-quick up {safe_iface}",
            f"systemctl enable wg-quick@{safe_iface} 2>/dev/null || true",
        ])

        mock_ssh.assert_called_once_with(server, expected_command)
