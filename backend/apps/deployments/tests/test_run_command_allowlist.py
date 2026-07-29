from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.servers import ManagedServer

try:
    from apps.deployments.views.server.helpers import _is_command_allowed
    _HAS_HELPER = True
except ImportError:
    _HAS_HELPER = False


def _assert_helper_or_endpoint(command, expected, viewset_call):
    if _HAS_HELPER:
        assert _is_command_allowed(command) is expected, (
            f"Helper expected {expected} for {command!r}"
        )
    else:
        response = viewset_call(command)
        if expected:
            assert response.status_code == 200, (
                f"Endpoint expected 200 for {command!r}, got {response.status_code}"
            )
        else:
            assert response.status_code == 403, (
                f"Endpoint expected 403 for {command!r}, got {response.status_code}"
            )


@pytest.mark.django_db(transaction=True)
class RunCommandAllowlistTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="runcmd_allowlist", password="123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="allowlist-test",
            host="203.0.113.30",
            api_url="https://allowlist.example.com",
            api_token="tok",
            ssh_user="root",
            ssh_password="ssh-pass",
        )
        self.url = f"/api/v1/servers/{self.server.id}/run_command/"

    def tearDown(self):
        self.server.delete()
        self.user.delete()

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

    def test_docker_ps_is_allowed(self):
        _assert_helper_or_endpoint(
            "docker ps", True, self._post
        )

    def test_docker_ps_a_is_allowed(self):
        _assert_helper_or_endpoint(
            "docker ps -a", True, self._post
        )

    def test_docker_logs_is_allowed(self):
        _assert_helper_or_endpoint(
            "docker logs my-container", True, self._post
        )

    def test_docker_inspect_is_allowed(self):
        _assert_helper_or_endpoint(
            "docker inspect my-container", True, self._post
        )

    def test_docker_exec_is_rejected(self):
        _assert_helper_or_endpoint(
            "docker exec my-container bash", False, self._post
        )

    def test_docker_run_is_rejected(self):
        _assert_helper_or_endpoint(
            "docker run alpine echo hi", False, self._post
        )

    def test_docker_rm_command_substitution_is_rejected(self):
        _assert_helper_or_endpoint(
            "docker rm -f $(docker ps -aq)", False, self._post
        )

    def test_docker_system_prune_is_rejected(self):
        _assert_helper_or_endpoint(
            "docker system prune", False, self._post
        )

    def test_docker_compose_down_is_rejected(self):
        _assert_helper_or_endpoint(
            "docker compose down", False, self._post
        )

    def test_docker_compose_up_is_rejected(self):
        _assert_helper_or_endpoint(
            "docker compose up -d", False, self._post
        )

    def test_df_is_allowed(self):
        _assert_helper_or_endpoint(
            "df -h", True, self._post
        )

    def test_free_is_allowed(self):
        _assert_helper_or_endpoint(
            "free -m", True, self._post
        )

    def test_cat_env_without_grep_is_rejected(self):
        _assert_helper_or_endpoint(
            "cat /opt/smsly-hosting/.env", False, self._post
        )

    def test_cat_passwd_is_rejected(self):
        _assert_helper_or_endpoint(
            "cat /etc/passwd", False, self._post
        )

    def test_rm_rf_root_is_rejected(self):
        _assert_helper_or_endpoint(
            "rm -rf /", False, self._post
        )
