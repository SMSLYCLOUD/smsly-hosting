import pytest
from django.test import TestCase
import os


from unittest.mock import patch, MagicMock
from apps.deployments.services.provisioner import provision_server
from apps.deployments.models import ManagedServer
from django.contrib.auth import get_user_model


@pytest.mark.django_db(transaction=True)
class TestE2EClusterSimulator(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="e2e_admin", password="123", is_superuser=True)

    def tearDown(self):
        self.user.delete()

    @patch("apps.deployments.services.provisioner._get_ssh_client")
    @patch("apps.deployments.services.provisioner._load_install_script", return_value=("echo OK", "test.sh"))
    @patch("apps.deployments.services.provisioner._prepare_remote_install_lock")
    @patch("apps.deployments.services.provisioner.requests.post")
    @patch("apps.deployments.services.provisioner._build_local_source_bundle")
    @patch("apps.deployments.services.provisioner._schedule_remote_reboot", return_value=False)
    def test_scenario_01_multi_node_provisioning(self, mock_reboot, mock_build, mock_requests_post, mock_lock, mock_load_script, mock_ssh):
        # Create 5 nodes
        servers = []
        for i in range(5):
            srv = ManagedServer.objects.create(
                owner=self.user,
                name=f"node-{i}",
                host=f"10.0.0.{10+i}",
                api_url="",
                provision_status=ManagedServer.ProvisionStatus.PENDING,
            )
            servers.append(srv)

        # Mock SSH channel returning credentials
        mock_channel = MagicMock()
        import itertools
        mock_channel.recv_ready.side_effect = itertools.cycle([True, False])
        mock_channel.recv.return_value = b"[cred] Credentials saved. api_url=http://mock api_token=smsly_123"
        mock_channel.exit_status_ready.return_value = True
        mock_channel.recv_exit_status.return_value = 0

        mock_ssh.return_value.get_transport.return_value.open_session.return_value = mock_channel

        mock_exec = MagicMock()
        mock_exec.channel.recv_exit_status.return_value = 0
        mock_exec.read.return_value = b"api_url=http://mock api_token=smsly_123"
        mock_ssh.return_value.exec_command.return_value = (MagicMock(), mock_exec, mock_exec)

        mock_build.return_value = "dummy.tar.gz"

        # Provision sequential to avoid sqlite lock and easily test
        for srv in servers:
            try:
                # Provide a fake file for the bundle
                with open("dummy.tar.gz", "w") as f:
                    f.write("test")
                with patch("os.path.getsize", return_value=4):
                    provision_server(str(srv.id))
            finally:
                if os.path.exists("dummy.tar.gz"):
                    os.remove("dummy.tar.gz")

        # Verify success
        for srv in servers:
            srv.refresh_from_db()
            self.assertEqual(srv.provision_status, ManagedServer.ProvisionStatus.DONE)
            self.assertEqual(srv.status, ManagedServer.Status.ONLINE)

        # Cleanup
        for srv in servers:
            srv.delete()

    @patch("apps.deployments.services.provisioner._get_ssh_client")
    @patch("apps.deployments.services.provisioner._load_install_script", return_value=("echo OK", "test.sh"))
    @patch("apps.deployments.services.provisioner._prepare_remote_install_lock")
    @patch("apps.deployments.services.provisioner.requests.post")
    @patch("apps.deployments.services.provisioner._build_local_source_bundle")
    @patch("apps.deployments.services.provisioner._schedule_remote_reboot", return_value=False)
    def test_scenario_08_partial_node_failure_recovery(self, mock_reboot, mock_build, mock_requests_post, mock_lock, mock_load_script, mock_ssh):
        # Create 1 node
        srv = ManagedServer.objects.create(
            owner=self.user,
            name="failing-node",
            host="10.0.0.99",
            api_url="",
            provision_status=ManagedServer.ProvisionStatus.PENDING,
        )

        # Mock SSH channel returning failure first time, then success
        mock_channel = MagicMock()
        mock_channel.recv_ready.side_effect = itertools.cycle([True, False])
        mock_channel.recv.return_value = b"Error during installation"
        mock_channel.exit_status_ready.return_value = True
        mock_channel.recv_exit_status.return_value = 1

        mock_ssh.return_value.get_transport.return_value.open_session.return_value = mock_channel

        mock_exec = MagicMock()
        mock_exec.channel.recv_exit_status.return_value = 0
        mock_exec.read.return_value = b"api_url=http://mock api_token=smsly_123"
        mock_ssh.return_value.exec_command.return_value = (MagicMock(), mock_exec, mock_exec)

        mock_build.return_value = "dummy.tar.gz"

        try:
            with open("dummy.tar.gz", "w") as f: f.write("test")
            with patch("os.path.getsize", return_value=4):
                try:
                    provision_server(str(srv.id))
                except Exception:
                    pass
        finally:
            if os.path.exists("dummy.tar.gz"): os.remove("dummy.tar.gz")

        srv.refresh_from_db()
        self.assertEqual(srv.provision_status, ManagedServer.ProvisionStatus.FAILED)

        # Reset the mock for success
        mock_channel = MagicMock()
        mock_channel.recv_ready.side_effect = [True, False] * 100
        mock_channel.recv.return_value = b"[cred] Credentials saved. api_url=http://mock api_token=smsly_123"
        mock_channel.exit_status_ready.return_value = True
        mock_channel.recv_exit_status.return_value = 0

        mock_ssh.return_value.get_transport.return_value.open_session.return_value = mock_channel

        try:
            with open("dummy.tar.gz", "w") as f: f.write("test")
            with patch("os.path.getsize", return_value=4):
                provision_server(str(srv.id))
        finally:
            if os.path.exists("dummy.tar.gz"): os.remove("dummy.tar.gz")

        srv.refresh_from_db()
        self.assertEqual(srv.provision_status, ManagedServer.ProvisionStatus.DONE)

        srv.delete()
