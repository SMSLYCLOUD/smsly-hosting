from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.deployments.models import PlatformConfig, Service
from apps.deployments.models_mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models_servers import ManagedServer
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.server_guard import ServerGuard
from apps.deployments.services.transfer_service import ServerTransferService
from apps.deployments.tasks_mesh import deploy_mesh_task
from apps.deployments.tasks_replication import deploy_replication_task


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

    @patch("apps.deployments.views_transfer.execute_server_transfer_task.delay")
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

    @patch("apps.deployments.services.transfer_service.BackupService.backup_service")
    @patch("apps.deployments.services.transfer_service.SSHClient")
    def test_transfer_failure_preserves_source_deployment(self, ssh_cls, backup_mock):
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

    @patch("apps.deployments.services.replication_service.ReplicationService._deploy_haproxy_local")
    @patch("apps.deployments.services.replication_service.ReplicationService._deploy_patroni_remote")
    @patch("apps.deployments.services.replication_service.ReplicationService._deploy_patroni_local")
    def test_replication_worker_a_worker_b_status_visible(
        self,
        deploy_local,
        deploy_remote,
        deploy_haproxy,
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

        def fake_get(url, timeout):
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
