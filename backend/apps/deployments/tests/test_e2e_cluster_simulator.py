import contextlib
import itertools
import os
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer
from apps.deployments.services.provisioner import provision_server


@pytest.mark.django_db(transaction=True)
class TestE2EClusterSimulator(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="e2e_admin", password="123", is_superuser=True)
        # Isolate host-side effects: the simulator mocks SSH, so anything
        # touching the orchestrator host (docker, iptables-adjacent deploys,
        # mesh DB rows + celery delay, node exporters over the network)
        # must be stubbed or the simulation is neither hermetic nor fast.
        self._host_patches = [
            patch("apps.deployments.services.provisioner.helpers._restrict_ssh_key_to_master_ip"),
            patch("apps.deployments.services.provisioner.core.provision_server._ensure_docker_mirror"),
            patch("apps.deployments.services.provisioner.core.provision_server._stop_docker_mirror"),
            patch(
                "apps.deployments.services.wireguard_service.WireGuardService.ensure_server_in_default_mesh",
                return_value={"wg_address": "", "peer": None},
            ),
            patch("apps.autoscaler.services.prometheus_targets.deploy_cadvisor_on_node", return_value=True),
            patch("apps.autoscaler.services.prometheus_targets.deploy_node_exporter_on_node", return_value=True),
            patch("apps.autoscaler.services.prometheus_targets.deploy_promtail_on_node", return_value=True),
            patch("apps.autoscaler.services.prometheus_targets.deploy_docker_labels_exporter_on_node", return_value=True),
            patch("apps.autoscaler.services.prometheus_targets.write_docker_labels_targets"),
        ]
        for _p in self._host_patches:
            _p.start()
        self.addCleanup(self._stop_host_patches)

    def _stop_host_patches(self):
        for _p in getattr(self, "_host_patches", []):
            with contextlib.suppress(Exception):
                _p.stop()

    def tearDown(self):
        self.user.delete()

    @patch("apps.deployments.services.provisioner.helpers._get_ssh_client")
    @patch("apps.deployments.services.provisioner.core.provision_server._load_install_script", return_value=("echo OK", "test.sh"))
    @patch("apps.deployments.services.provisioner.core.provision_server._prepare_remote_install_lock")
    @patch("apps.deployments.services.provisioner.core.provision_server.requests.post")
    @patch("apps.deployments.services.provisioner.core.provision_server._build_local_source_bundle")
    @patch("apps.deployments.services.provisioner.core.provision_server._schedule_remote_reboot", return_value=False)
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
        mock_exec.read.return_value = (
            b"api_token=smsly_123\n"
            b"django_superuser_password=s3cret-test-only\n"
        )
        mock_ssh.return_value.exec_command.return_value = (MagicMock(), mock_exec, mock_exec)

        mock_build.return_value = "dummy.tar.gz"

        # The simulator has no live node API: shape requests.post as a
        # failed response. Otherwise response.json().get("token") returns
        # a truthy MagicMock that poisons server.api_token and breaks
        # every subsequent save with FieldError.
        mock_requests_post.return_value.ok = False
        mock_requests_post.return_value.status_code = 500
        mock_requests_post.return_value.json.return_value = {}
        mock_requests_post.return_value.content = b""

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

    @patch("apps.deployments.services.provisioner.helpers._get_ssh_client")
    @patch("apps.deployments.services.provisioner.core.provision_server._load_install_script", return_value=("echo OK", "test.sh"))
    @patch("apps.deployments.services.provisioner.core.provision_server._prepare_remote_install_lock")
    @patch("apps.deployments.services.provisioner.core.provision_server.requests.post")
    @patch("apps.deployments.services.provisioner.core.provision_server._build_local_source_bundle")
    @patch("apps.deployments.services.provisioner.core.provision_server._schedule_remote_reboot", return_value=False)
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
        mock_exec.read.return_value = (
            b"api_token=smsly_123\n"
            b"django_superuser_password=s3cret-test-only\n"
        )
        mock_ssh.return_value.exec_command.return_value = (MagicMock(), mock_exec, mock_exec)

        mock_build.return_value = "dummy.tar.gz"

        # No live node API in the simulator: failed-response shape so the
        # token-exchange blocks log warnings instead of poisoning
        # server.api_token with a truthy MagicMock (FieldError on save).
        mock_requests_post.return_value.ok = False
        mock_requests_post.return_value.status_code = 500
        mock_requests_post.return_value.json.return_value = {}
        mock_requests_post.return_value.content = b""

        try:
            with open("dummy.tar.gz", "w") as f:
                f.write("test")
            with patch("os.path.getsize", return_value=4):
                with contextlib.suppress(Exception):
                    provision_server(str(srv.id))
        finally:
            if os.path.exists("dummy.tar.gz"):
                os.remove("dummy.tar.gz")

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
            with open("dummy.tar.gz", "w") as f:
                f.write("test")
            with patch("os.path.getsize", return_value=4):
                provision_server(str(srv.id))
        finally:
            if os.path.exists("dummy.tar.gz"):
                os.remove("dummy.tar.gz")

        srv.refresh_from_db()
        self.assertEqual(srv.provision_status, ManagedServer.ProvisionStatus.DONE)

        srv.delete()
