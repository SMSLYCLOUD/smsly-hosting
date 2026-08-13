from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from apps.deployments.models import Service
from apps.deployments.models.addons import Addon
from apps.deployments.tasks.deploy.addons import _probe_addon_connectivity


@pytest.mark.django_db
@patch("apps.deployments.tasks.deploy.addons.docker.from_env")
def test_addon_connectivity_probe_uses_supported_exec_run_arguments(mock_docker_from_env):
    user = get_user_model().objects.create(username="addon-probe")
    service = Service.objects.create(name="addon-probe", owner=user)
    Addon.objects.create(
        service=service,
        name="redis",
        addon_type="REDIS",
        status="ACTIVE",
        connection_url="redis://redis:6379/0",
    )

    container = MagicMock()
    container.exec_run.return_value = SimpleNamespace(exit_code=0, output=b"OK\n")
    mock_docker_from_env.return_value.containers.get.return_value = container

    assert _probe_addon_connectivity(service, "container-id") == []
    args, kwargs = container.exec_run.call_args
    assert args[0][0:2] == ["bash", "-c"]
    assert "timeout" not in kwargs
