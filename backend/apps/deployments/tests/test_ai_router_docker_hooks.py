from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from apps.deployments.models import Deployment, EnvironmentVariable, Service
from apps.deployments.tasks.deployment.tasks_deploy import _run_managed_image_post_deploy_hooks

User = get_user_model()


@pytest.mark.django_db
@patch("apps.deployments.tasks.deploy.build._wait_for_local_container_healthy", return_value=True)
@patch("apps.deployments.tasks.deploy.build.subprocess.run")
@patch("apps.deployments.tasks.deploy.build.docker.from_env")
def test_ai_router_docker_hooks_sync_live_router_config(
    mock_docker_from_env,
    mock_run,
    _wait_mock,
):
    user = User.objects.create(username="router-hooks")
    service = Service.objects.create(
        name="ai-router-demo",
        owner=user,
        docker_image="ghcr.io/berriai/litellm:main-stable",
    )
    deployment = Deployment.objects.create(service=service, status="HEALTH_CHECK", commit_hash="test")
    ollama = Service.objects.create(
        name="ollama-phi3-demo",
        owner=user,
        docker_image="ollama/ollama:latest",
        internal_port=11434,
        project=service.project,
    )
    Deployment.objects.create(service=ollama, status="ACTIVE", commit_hash="test")
    EnvironmentVariable.objects.create(service=service, key="LITELLM_MASTER_KEY", value="router-key", is_secret=True)
    EnvironmentVariable.objects.create(service=service, key="AI_ROUTER_API_BASE", value="/api/v1", is_secret=False)
    EnvironmentVariable.objects.create(service=ollama, key="OLLAMA_MODEL", value="phi3", is_secret=False)

    container = MagicMock()
    container.name = "ai-router-demo"
    mock_docker_from_env.return_value.containers.get.return_value = container

    mock_run.side_effect = [
        SimpleNamespace(returncode=0, stdout="", stderr=""),  # docker cp
        SimpleNamespace(returncode=0, stdout="", stderr=""),  # docker restart
    ]

    _run_managed_image_post_deploy_hooks(deployment, service, "container-id")

    copy_cmd = mock_run.call_args_list[0].args[0]
    restart_cmd = mock_run.call_args_list[1].args[0]
    assert copy_cmd[:2] == ["docker", "cp"]
    assert copy_cmd[-1] == "ai-router-demo:/app/proxy_server_config.yaml"
    assert restart_cmd == ["docker", "restart", "ai-router-demo"]
