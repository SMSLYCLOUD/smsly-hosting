# pylint: disable=invalid-name
"""Tests for SEC (Issue 75): docker logs is restricted to containers the user owns."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models.core import Service
from apps.deployments.models.servers import ManagedServer

User = get_user_model()


class DockerLogsScopeTests(TestCase):
    def setUp(self):
        from apps.deployments import views_servers
        views_servers._DOCKER_LOGS_OWNER_CACHE.clear()
        self.user_a = User.objects.create_user(
            username="logs-a", email="a@e.com", password="pw"
        )
        self.user_b = User.objects.create_user(
            username="logs-b", email="b@e.com", password="pw"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user_a)
        self.server = ManagedServer.objects.create(
            owner=self.user_a,
            name="logs-srv",
            host="203.0.113.1",
            api_url="https://logs.example.com",
            api_token="tok",
            ssh_user="root",
            ssh_password="ssh",
        )
        self.url = f"/api/v1/servers/{self.server.id}/run_command/"
        provider = CloudProvider.objects.create(
            name="logs-prov", provider_type="LOCAL", is_active=True
        )
        # User A owns a service named "my-app"
        self.owned_service = Service.objects.create(
            name="my-app",
            owner=self.user_a,
            repository_url="https://github.com/x/y",
            provider=provider,
        )
        # User B owns a service named "their-app"
        self.other_service = Service.objects.create(
            name="their-app",
            owner=self.user_b,
            repository_url="https://github.com/x/y",
            provider=provider,
        )

    def _post(self, command):
        with patch(
            "apps.deployments.services.self_healing_orchestrator.SelfHealingOrchestrator"
        ) as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch._exec.return_value = ("", "", 0)
            mock_orch_cls.return_value = mock_orch
            return self.client.post(
                self.url, {"command": command}, format="json"
            )

    def test_docker_logs_for_owned_container_is_allowed(self):
        response = self._post("docker logs my-app")
        self.assertEqual(response.status_code, 200)

    def test_docker_logs_for_other_users_container_is_rejected(self):
        response = self._post("docker logs their-app")
        self.assertEqual(response.status_code, 403)

    def test_docker_logs_for_unknown_container_is_rejected(self):
        response = self._post("docker logs does-not-exist")
        self.assertEqual(response.status_code, 403)

    def test_docker_ps_is_still_allowed(self):
        response = self._post("docker ps -a")
        self.assertEqual(response.status_code, 200)

    def test_docker_logs_with_flag_is_rejected(self):
        """A ``docker logs --tail 100 foo`` invocation is rejected — flag follows subcommand."""
        response = self._post("docker logs --tail 100 my-app")
        self.assertEqual(response.status_code, 403)
