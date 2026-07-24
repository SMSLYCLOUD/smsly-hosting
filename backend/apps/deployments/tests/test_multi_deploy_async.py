from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, ManagedServer, Service


class AsyncMultiDeployTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="testuser", password="password123")
        self.client.force_authenticate(user=self.user)

        # Create target remote server
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="remote-server-1",
            host="192.168.1.100",
            api_url="https://remote-server-1.example.com",
            api_token="smsly_token",
            gateway_secret="gateway-secret",
            allow_user_workloads=True,
            status="ACTIVE",
        )

        # Create provider
        self.provider = CloudProvider.objects.create(
            name="Test Provider",
            provider_type=CloudProvider.ProviderType.REMOTE,
            is_active=True,
        )

        # Create service
        self.service = Service.objects.create(
            owner=self.user,
            name="test-multi-app",
            repository_url="https://github.com/example/test-multi-app",
            branch="main",
            server=self.server,
            provider=self.provider,
        )

    @patch("apps.deployments.views.service.deploy.smart_deploy_task.delay")
    def test_multi_deploy_asynchronous_queuing(self, mock_delay):
        url = reverse("service-multi-deploy", kwargs={"pk": str(self.service.id)})
        data = {
            "ref": "main",
            "server_ids": [str(self.server.id)],
            "include_local": False
        }

        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        # Check response content
        res_data = response.json()
        self.assertIn("remotes", res_data)
        self.assertEqual(len(res_data["remotes"]), 1)
        remote_res = res_data["remotes"][0]
        self.assertEqual(remote_res["server_id"], str(self.server.id))
        self.assertEqual(remote_res["status"], "queued")
        self.assertIn("deployment", remote_res)

        # Verify a local tracking deployment was created
        deployments = Deployment.objects.filter(service=self.service, target_server=self.server)
        self.assertEqual(deployments.count(), 1)
        deployment = deployments.first()
        self.assertEqual(deployment.status, Deployment.Status.QUEUED)
        self.assertFalse(deployment.target_is_local)

        # Verify smart_deploy_task.delay was dispatched
        mock_delay.assert_called_once_with(
            deployment_id=str(deployment.id),
            provider_id=str(self.provider.id)
        )

    def test_node_metadata_serialization(self):
        # 1. Before deployment, metadata should fall back to service.server details
        url = reverse("service-detail", kwargs={"pk": str(self.service.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        metadata = response.json().get("node_metadata")
        self.assertIsNotNone(metadata)
        self.assertEqual(metadata.get("id"), str(self.server.id))
        self.assertEqual(metadata.get("name"), "remote-server-1")
        self.assertEqual(metadata.get("target_type"), "Remote Server")
        self.assertEqual(metadata.get("host"), "192.168.1.100")

        # 2. After deployment completes and active_target_type is set to "remote"
        self.service.active_target_type = "remote"
        self.service.active_host_ip = "192.168.1.200"
        self.service.save()

        response = self.client.get(url)
        metadata = response.json().get("node_metadata")
        self.assertEqual(metadata.get("id"), str(self.server.id))
        self.assertEqual(metadata.get("name"), "remote-server-1")
        self.assertEqual(metadata.get("target_type"), "Remote Server")
        self.assertEqual(metadata.get("host"), "192.168.1.200")

    def test_node_metadata_prefers_latest_verified_remote_deploy_over_stale_local_marker(self):
        self.service.server = None
        self.service.active_target_type = "local"
        self.service.active_host_ip = "127.0.0.1"
        self.service.save(update_fields=["server", "active_target_type", "active_host_ip"])

        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash="4bd993c",
            target_server=self.server,
            target_is_local=False,
            verified_target_type="remote",
            verified_host_ip="10.150.0.2",
            verified_runtime_id="container-id",
        )

        url = reverse("service-detail", kwargs={"pk": str(self.service.id)})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        metadata = response.json().get("node_metadata")
        self.assertEqual(metadata.get("id"), str(self.server.id))
        self.assertEqual(metadata.get("name"), "remote-server-1")
        self.assertEqual(metadata.get("target_type"), "Remote Server")
        self.assertEqual(metadata.get("host"), "10.150.0.2")
