# pylint: disable=invalid-name
"""Addon deploy gates must not use exec probes and must support shared addons.

Contract (post exec-probe removal):
- _probe_addon_connectivity never runs code inside containers (no exec_run).
- Shared POSTGRES addons (logical DBs, no per-addon container) are
  verified against the shared server (or tenant pooler), never against
  `smsly-addon-<type>-<id>` — checking that name failed every shared
  deploy with "container ... is not running (cid=None)".
"""
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from apps.deployments.models import Deployment, Service
from apps.deployments.models.addons import Addon
from apps.deployments.tasks.deploy import addons as addons_mod
from apps.deployments.tasks.deploy.addons import _probe_addon_connectivity


def _make_container(status='running', networks=None):
    container = MagicMock()
    container.status = status
    container.attrs = {'NetworkSettings': {'Networks': networks or {}}}
    container.exec_run = MagicMock()
    return container


@pytest.mark.django_db
def test_probe_never_execs_into_containers():
    user = get_user_model().objects.create(username="addon-probe-noexec")
    service = Service.objects.create(name="addon-probe-noexec", owner=user)
    Addon.objects.create(
        service=service,
        name="redis",
        addon_type="REDIS",
        status="ACTIVE",
        connection_url="redis://redis:6379/0",
    )
    net = {"net1": {"Aliases": ["redis"]}}
    svc_container = _make_container(networks={"net1": {}})
    addon_container = _make_container(networks=net)

    def _get(name):
        if name == "container-id":
            return svc_container
        return addon_container

    with patch.object(addons_mod.docker, "from_env") as mock_from_env:
        mock_from_env.return_value.containers.get.side_effect = _get
        assert _probe_addon_connectivity(service, "container-id") == []
    svc_container.exec_run.assert_not_called()
    addon_container.exec_run.assert_not_called()


@pytest.mark.django_db
def test_probe_shared_postgres_checks_shared_server():
    """Regression: shared addons must not be checked as per-addon containers."""
    from apps.addons.services.shared_postgres import SHARED_CONTAINER
    user = get_user_model().objects.create(username="addon-probe-shared")
    service = Service.objects.create(name="addon-probe-shared", owner=user)
    Addon.objects.create(
        service=service,
        name="postgres-addon-probe-shared",
        addon_type="POSTGRES",
        status="ACTIVE",
        provision_mode="shared",
        connection_url="postgresql://u:p@postgres-addon-probe-shared:5432/db",
    )
    svc_container = _make_container(networks={"smsly-net": {}})
    shared_container = _make_container(
        networks={"smsly-net": {"Aliases": ["postgres-addon-probe-shared"]}})

    seen = []

    def _get(name):
        seen.append(name)
        if name == "container-id":
            return svc_container
        if name == SHARED_CONTAINER:
            return shared_container
        raise AssertionError(f"unexpected container lookup: {name}")

    with patch.object(addons_mod.docker, "from_env") as mock_from_env:
        mock_from_env.return_value.containers.get.side_effect = _get
        assert _probe_addon_connectivity(service, "container-id") == []
    assert SHARED_CONTAINER in seen
    assert not any(str(n).startswith("smsly-addon-postgres-") for n in seen)


@pytest.mark.django_db
def test_probe_shared_postgres_down_reports_shared_server():
    from apps.addons.services.shared_postgres import SHARED_CONTAINER
    import docker as docker_lib
    user = get_user_model().objects.create(username="addon-probe-shareddown")
    service = Service.objects.create(name="addon-probe-shareddown", owner=user)
    Addon.objects.create(
        service=service,
        name="pg",
        addon_type="POSTGRES",
        status="ACTIVE",
        provision_mode="shared",
        connection_url="postgresql://u:p@pg:5432/db",
    )
    svc_container = _make_container(networks={"smsly-net": {}})

    def _get(name):
        if name == "container-id":
            return svc_container
        raise docker_lib.errors.NotFound("No such container")

    with patch.object(addons_mod.docker, "from_env") as mock_from_env:
        mock_from_env.return_value.containers.get.side_effect = _get
        errors = _probe_addon_connectivity(service, "container-id")
    assert len(errors) == 1
    assert SHARED_CONTAINER in errors[0]
    assert "smsly-addon-postgres-" not in errors[0]


@pytest.mark.django_db
def test_probe_pooler_routed_checks_pooler():
    user = get_user_model().objects.create(username="addon-probe-pooler")
    service = Service.objects.create(name="addon-probe-pooler", owner=user)
    Addon.objects.create(
        service=service,
        name="pg",
        addon_type="POSTGRES",
        status="ACTIVE",
        provision_mode="shared",
        pooler_routed=True,
        connection_url="postgresql://u:p@pg:5432/db",
    )
    svc_container = _make_container(networks={"smsly-net": {}})
    pooler_container = _make_container(
        networks={"smsly-net": {"Aliases": ["pg"]}})

    seen = []

    def _get(name):
        seen.append(name)
        if name == "container-id":
            return svc_container
        if name == "pooler-1":
            return pooler_container
        import docker as docker_lib
        raise docker_lib.errors.NotFound("No such container")

    with patch.object(addons_mod.docker, "from_env") as mock_from_env, \
         patch("apps.addons.services.tenant_pooler.tenants_container_name",
               return_value="pooler-1"):
        mock_from_env.return_value.containers.get.side_effect = _get
        assert _probe_addon_connectivity(service, "container-id") == []
    assert "pooler-1" in seen


@pytest.mark.django_db
def test_ensure_addons_ready_shared_checks_shared_server():
    """_ensure_addons_ready must not demand a per-addon container for shared."""
    from apps.deployments.models import Deployment as DeploymentModel
    user = get_user_model().objects.create(username="addon-ensure-shared")
    service = Service.objects.create(name="addon-ensure-shared", owner=user)
    Addon.objects.create(
        service=service,
        name="pg",
        addon_type="POSTGRES",
        status="ACTIVE",
        provision_mode="shared",
        connection_url="postgresql://u:p@pg:5432/db",
    )
    deployment = DeploymentModel.objects.create(
        service=service, status=DeploymentModel.Status.BUILDING,
        commit_hash="shared1",
    )
    inspect_out = MagicMock(stdout="pg ", returncode=0)
    with patch("apps.addons.services.addon_provisioner.addon_provisioner._container_status",
               return_value=("cid123", True)) as mock_status, \
         patch("apps.deployments.tasks.deploy.addons.subprocess.run",
               return_value=inspect_out):
        addons_mod._ensure_addons_ready(service, deployment)  # must not raise
    (container_name,), _ = mock_status.call_args
    assert container_name != "smsly-addon-postgres-%s" % Addon.objects.get(name="pg").id
