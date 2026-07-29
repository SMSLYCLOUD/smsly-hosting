import base64
import shlex
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models.servers import ManagedServer
from apps.deployments.services.wireguard_service import WireGuardService


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
        User = get_user_model()
        user = User.objects.create_user(username="test2", password="test")
        server = ManagedServer.objects.create(name="test-server-2", host="1.1.1.2", owner=user)

        with self.assertRaises(ValueError):
            WireGuardService._deploy_remote(server, "[Interface]\nPrivateKey = test\n", "wg0")

        mock_ssh.assert_not_called()

    @patch('apps.deployments.services.wireguard_service.WireGuardService.deploy_config')
    def test_non_default_mesh_cannot_reuse_active_interface(self, deploy_config):
        default_mesh = MeshNetwork.objects.create(name="default", subnet="10.100.0.0/24")
        MeshNetwork.objects.create(
            name="transfer-mesh",
            subnet="10.150.0.0/24",
            interface_name=default_mesh.interface_name,
        )

        result = WireGuardService.deploy_full_mesh(
            MeshNetwork.objects.get(name="transfer-mesh")
        )

        self.assertEqual(result["success"], [])
        self.assertIn("already uses it", result["failed"][0]["error"])
        deploy_config.assert_not_called()

    @patch('apps.deployments.services.wireguard_service.WireGuardService._detect_local_endpoint',
           return_value='198.51.100.1:51820')
    @patch('apps.deployments.tasks.infra.tasks_mesh.deploy_mesh_task.delay')
    def test_ensure_server_in_default_mesh_adds_local_and_remote_peers(
        self, mock_deploy_mesh, _detect_endpoint
    ):
        User = get_user_model()
        user = User.objects.create_user(username="mesh-owner", password="test")
        primary = ManagedServer.objects.create(
            name="primary",
            host="198.51.100.1",
            owner=user,
            is_primary=True,
            status=ManagedServer.Status.ONLINE,
        )
        server = ManagedServer.objects.create(
            name="worker",
            host="203.0.113.50",
            owner=user,
            status=ManagedServer.Status.ONLINE,
            ssh_password="secret",
        )

        result = WireGuardService.ensure_server_in_default_mesh(server)

        mesh = MeshNetwork.objects.get(name="default")
        local_peer = WireGuardPeer.objects.get(mesh=mesh, is_local=True)
        remote_peer = WireGuardPeer.objects.get(mesh=mesh, server=server)
        self.assertEqual(WireGuardPeer.objects.filter(mesh=mesh, is_active=True).count(), 2)
        self.assertEqual(local_peer.endpoint, "198.51.100.1:51820")
        self.assertEqual(remote_peer.endpoint, "203.0.113.50:51820")
        self.assertEqual(result["wg_address"], remote_peer.wg_address)
        self.assertTrue(result["queued"])
        mock_deploy_mesh.assert_called_once_with(str(mesh.id))

        server.refresh_from_db()
        primary.refresh_from_db()
        self.assertEqual(server.wg_address, remote_peer.wg_address)
        self.assertEqual(primary.wg_address, local_peer.wg_address)

        second = WireGuardService.ensure_server_in_default_mesh(server)

        self.assertFalse(second["queued"])
        self.assertEqual(WireGuardPeer.objects.filter(mesh=mesh).count(), 2)

    @patch.dict('os.environ', {}, clear=True)
    def test_get_master_mesh_ip_fallback(self):
        from apps.deployments.services.provisioner import _get_master_mesh_ip
        User = get_user_model()
        user = User.objects.create_user(username="mesh-owner-fallback", password="test")

        primary = ManagedServer.objects.create(
            name="primary",
            host="198.51.100.1",
            owner=user,
            is_primary=True,
            status=ManagedServer.Status.ONLINE,
        )

        # 1. Initially wg_address is None, default fallback to 10.100.0.1 (since is_primary is True)
        self.assertEqual(_get_master_mesh_ip(), "10.100.0.1")

        # 2. Environment variable fallback
        with patch.dict('os.environ', {'MASTER_MESH_IP': '10.100.0.99'}):
            self.assertEqual(_get_master_mesh_ip(), "10.100.0.99")

        # 3. Database direct wg_address
        primary.wg_address = "10.100.0.5"
        primary.save()
        self.assertEqual(_get_master_mesh_ip(), "10.100.0.5")

