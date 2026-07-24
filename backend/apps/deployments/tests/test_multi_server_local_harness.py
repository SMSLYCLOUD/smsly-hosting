import os
import tempfile
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.deployments.models import PlatformConfig, Service
from apps.deployments.models.backup import ServiceBackup
from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models.servers import ManagedServer
from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services.server_guard import ServerGuard
from apps.deployments.services.transfer_service import ServerTransferService
from apps.deployments.tasks.infra.tasks_mesh import deploy_mesh_task
from apps.deployments.tasks.data.tasks_replication import deploy_replication_task


class MultiServerLocalHarnessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="harness-admin",
            email="harness@example.com",
            password="password123",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        PlatformConfig.load().save()
        cfg = PlatformConfig.load()
        cfg.server_ip = "10.0.0.10"
        cfg.save(update_fields=["server_ip"])

        self.primary = ManagedServer.objects.create(
            owner=self.user,
            name="control-plane",
            host="10.0.0.1",
            status=ManagedServer.Status.ONLINE,
            is_primary=True,
            allow_user_workloads=False,
        )
        self.worker_a = ManagedServer.objects.create(
            owner=self.user,
            name="worker-a",
            host="10.0.0.11",
            status=ManagedServer.Status.ONLINE,
            ssh_password="worker-a-root",
        )
        self.worker_b = ManagedServer.objects.create(
            owner=self.user,
            name="worker-b",
            host="10.0.0.12",
            private_ip="172.31.0.12",
            status=ManagedServer.Status.ONLINE,
            ssh_password="worker-b-root",
        )
        self.service = Service.objects.create(
            owner=self.user,
            name="harness-service",
            server=self.worker_a,
        )

    def test_target_selection_excludes_control_plane(self):
        eligible = ServerGuard.filter_user_workload_targets(
            ManagedServer.objects.filter(owner=self.user)
        )

        self.assertNotIn(self.primary, list(eligible))
        self.assertIn(self.worker_a, list(eligible))
        self.assertIn(self.worker_b, list(eligible))

    @patch("apps.core.views.transfer.execute_server_transfer_task.delay")
    def test_transfer_worker_a_to_worker_b_and_primary_rejection(self, delay_mock):
        response = self.client.post(
            reverse("transfer-list"),
            {
                "transfer_type": "SERVICE",
                "service_id": str(self.service.id),
                "target_server_id": str(self.worker_b.id),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transfer = ServerTransfer.objects.get(id=response.data["id"])
        self.assertEqual(transfer.source_server_ip, "10.0.0.10")
        self.assertEqual(transfer.target_server_ip, "10.0.0.12")
        self.service.refresh_from_db()
        self.assertEqual(self.service.server_id, self.worker_a.id)
        delay_mock.assert_called_once_with(str(transfer.id))

        blocked = self.client.post(
            reverse("transfer-list"),
            {
                "transfer_type": "SERVICE",
                "service_id": str(self.service.id),
                "target_server_id": str(self.primary.id),
            },
            format="json",
        )
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(blocked.data["error"]["code"], "PRIMARY_SERVER_DEPLOYMENT_BLOCKED")

    @patch("apps.deployments.services.transfer_service.ServerTransferService._sync_target_dashboard")
    @patch("apps.deployments.services.transfer_service.ServerTransferService._wait_for_remote_backend_ready")
    @patch("apps.deployments.services.transfer_service.BackupService.backup_service")
    @patch("apps.deployments.services.transfer_service.SSHClient")
    def test_transfer_failure_preserves_source_deployment(
        self,
        ssh_cls,
        backup_mock,
        _ready_mock,
        _sync_mock,
    ):
        ssh = ssh_cls.return_value
        ssh.connect.return_value = None
        ssh.check_docker.return_value = True
        ssh.exec_command.return_value = "smsly-hosting-backend-1"
        backup_mock.side_effect = RuntimeError("backup failed")

        transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type="SERVICE",
            service=self.service,
            source_server_ip="10.0.0.10",
            target_server_ip="10.0.0.12",
            target_ssh_password="worker-b-root",
        )

        ServerTransferService(transfer).execute()

        transfer.refresh_from_db()
        self.service.refresh_from_db()
        self.assertEqual(transfer.status, "FAILED")
        self.assertIn("backup failed", transfer.error_message)
        self.assertEqual(self.service.server_id, self.worker_a.id)

    @patch("apps.deployments.services.transfer_service.ServerTransferService._regenerate_master_caddyfile")
    @patch("apps.deployments.services.transfer_service.ServerTransferService._sync_target_dashboard")
    @patch("apps.deployments.services.transfer_service.ServerTransferService._verify_between_servers")
    @patch("apps.deployments.services.transfer_service.ServerTransferService._interconnect_servers")
    @patch("apps.deployments.services.transfer_service.ServerTransferService._wait_for_remote_backend_ready")
    @patch("apps.deployments.services.transfer_service.BackupService.backup_service")
    @patch("apps.deployments.services.transfer_service.time.sleep", return_value=None)
    @patch("apps.deployments.services.transfer_service.SSHClient")
    def test_service_transfer_execute_completes_and_moves_service_to_target(
        self,
        ssh_cls,
        _sleep_mock,
        backup_mock,
        _ready_mock,
        _mesh_mock,
        _reachability_mock,
        _sync_mock,
        _caddy_mock,
    ):
        self.service.docker_image = "registry.local/harness-service:abc123"
        self.service.public_domain = "harness-service.example.test"
        self.service.internal_port = 8080
        self.service.save(update_fields=["docker_image", "public_domain", "internal_port"])

        backup_file = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        try:
            backup_file.write(b"fake-transfer-archive")
            backup_file.close()
            backup = ServiceBackup.objects.create(
                service=self.service,
                created_by=self.user,
                status="COMPLETED",
                backup_type="PRE_TRANSFER",
                file_path=backup_file.name,
                metadata={
                    "docker_image": "registry.local/harness-service:abc123",
                    "env_vars": [{"key": "PORT", "value": "8080"}],
                    "volumes": [],
                },
            )
            backup_mock.return_value = backup

            ssh = ssh_cls.return_value
            ssh.connect.return_value = None
            ssh.close.return_value = None
            ssh.check_docker.return_value = True
            ssh.upload_file.return_value = None

            executed = []

            def exec_side_effect(command, *args, **kwargs):
                executed.append(command)
                if "docker ps --filter name=backend" in command:
                    return "smsly-hosting-backend-1\n"
                if "restore_trigger" in command and "python3" in command:
                    return "SUCCESS\n"
                if "docker inspect -f '{{.Id}}'" in command:
                    return "container-abc123\n"
                if "docker inspect -f '{{.State.Running}}'" in command:
                    return "true\n"
                if "SMSLY_ROUTE_HTTP" in command:
                    return "SMSLY_ROUTE_HTTP:200\n"
                return ""

            ssh.exec_command.side_effect = exec_side_effect

            transfer = ServerTransfer.objects.create(
                owner=self.user,
                transfer_type="SERVICE",
                service=self.service,
                source_server_ip="10.0.0.10",
                target_server_ip="10.0.0.12",
                target_ssh_password="worker-b-root",
            )

            ServerTransferService(transfer).execute()

            transfer.refresh_from_db()
            self.service.refresh_from_db()
            print("TRANSFER ERROR MESSAGE:", transfer.error_message)
            print("TRANSFER LOGS:", transfer.logs)
            self.assertEqual(transfer.status, "COMPLETED")
            self.assertEqual(transfer.progress_percent, 100)
            self.assertEqual(transfer.source_backup_id, backup.id)
            self.assertEqual(self.service.server_id, self.worker_b.id)
            self.assertEqual(transfer.target_ssh_password, "")
            self.assertIsNotNone(transfer.rollback_deadline)
            ssh.upload_file.assert_any_call(
                backup_file.name,
                f"/tmp/{os.path.basename(backup_file.name)}",
            )
            self.assertTrue(
                any("docker run" in command and "registry.local/harness-service:abc123" in command
                    for command in executed)
            )
        finally:
            if os.path.exists(backup_file.name):
                os.remove(backup_file.name)

    def _mesh(self):
        mesh = MeshNetwork.objects.create(name="harness-mesh", subnet="10.100.0.0/24")
        WireGuardPeer.objects.create(
            mesh=mesh,
            is_local=True,
            is_active=True,
            wg_address="10.100.0.1",
            private_key="local-private",
            public_key="l" * 44,
        )
        WireGuardPeer.objects.create(
            mesh=mesh,
            server=self.worker_a,
            is_active=True,
            wg_address="10.100.0.2",
            private_key="worker-a-private",
            public_key="a" * 44,
        )
        WireGuardPeer.objects.create(
            mesh=mesh,
            server=self.worker_b,
            is_active=True,
            wg_address="10.100.0.3",
            private_key="worker-b-private",
            public_key="b" * 44,
        )
        return mesh

    @patch(
        "apps.deployments.services.wireguard_service.WireGuardService.deploy_full_mesh",
        return_value={"success": [], "failed": []},
    )
    def test_transfer_interconnect_reuses_default_mesh(self, deploy_full_mesh):
        transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type="SERVICE",
            service=self.service,
            source_server_ip="10.0.0.10",
            target_server_ip="10.0.0.12",
            target_ssh_password="worker-b-root",
        )

        ServerTransferService(transfer)._interconnect_servers()

        self.assertFalse(MeshNetwork.objects.filter(name="transfer-mesh").exists())
        mesh = MeshNetwork.objects.get(name="default")
        self.assertEqual(mesh.interface_name, "wg0")
        self.assertEqual(mesh.subnet, "10.100.0.0/24")
        self.assertTrue(mesh.peers.filter(is_local=True, is_active=True).exists())
        self.assertTrue(
            mesh.peers.filter(server=self.worker_b, is_active=True).exists()
        )
        self.worker_b.refresh_from_db()
        self.assertEqual(self.worker_b.wg_address, "10.100.0.2")
        deploy_full_mesh.assert_called_once_with(mesh)

    @patch("apps.deployments.services.wireguard_service.WireGuardService.deploy_config")
    def test_mesh_peer_registration_and_status_flow(self, deploy_config):
        mesh = self._mesh()

        result = deploy_mesh_task(str(mesh.id))

        mesh.refresh_from_db()
        self.assertEqual(result["failed"], [])
        self.assertEqual(mesh.mesh_status, "ACTIVE")
        self.assertEqual(mesh.peers.filter(is_active=True).count(), 3)

        response = self.client.get(reverse("mesh-detail", args=[mesh.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["mesh_status"], "ACTIVE")

    @patch(
        "apps.deployments.services.replication_service.ReplicationService.wait_for_cluster_ready",
        return_value={"status": "READY", "health": {"primary": {"name": "patroni1"}}},
    )
    @patch("apps.deployments.services.replication_service.ReplicationService._deploy_haproxy_local")
    @patch("apps.deployments.services.replication_service.ReplicationService._deploy_patroni_remote")
    @patch("apps.deployments.services.replication_service.ReplicationService._deploy_patroni_local")
    def test_replication_worker_a_worker_b_status_visible(
        self,
        deploy_local,
        deploy_remote,
        deploy_haproxy,
        _ready_mock,
    ):
        mesh = self._mesh()

        result = deploy_replication_task(
            str(mesh.id),
            "db-password",
            "admin-password",
            "unique-repl-password",
        )

        mesh.refresh_from_db()
        self.assertEqual(mesh.replication_status, "ACTIVE")
        self.assertEqual(result["haproxy"], "OK")
        self.assertEqual(len(result["patroni"]), 3)

        response = self.client.get(reverse("mesh-detail", args=[mesh.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["replication_status"], "ACTIVE")

    @patch("requests.get")
    def test_replication_sync_now_updates_db_state(self, requests_get):
        mesh = self._mesh()

        def fake_get(url, *args, **kwargs):
            response = Mock()
            response.status_code = 200
            if "10.100.0.1" in url:
                response.json.return_value = {
                    "role": "master",
                    "state": "running",
                    "xlog": {"location": "0/16B5D48"},
                }
            else:
                response.json.return_value = {
                    "role": "replica",
                    "state": "streaming",
                    "xlog": {"received_location": "0/16B5D48"},
                }
            return response

        requests_get.side_effect = fake_get
        response = self.client.post(
            reverse("replication-sync-now"),
            {"mesh_id": str(mesh.id)},
            format="json",
        )

        mesh.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(mesh.replication_status, "ACTIVE")
        self.assertIn("nodes", mesh.replication_last_result)
