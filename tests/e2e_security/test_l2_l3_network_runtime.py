"""E2E security tests for L2 (`smsly-platform-net` isolation) & L3 (`promote_container` & `runsc` DNS)."""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.cloud.adapters.local import LocalAdapter
from apps.deployments.models import Project, Service
from apps.deployments.services import container_runtime as cr_mod
from apps.deployments.services import network_scope as ns_mod
from apps.deployments.services.container_refresh import recreate_with_fresh_env

pytestmark = [pytest.mark.security]


# ============================================================================
# F3: L2 smsly-platform-net lateral movement isolation
# ============================================================================


def test_f3_service_use_internal_network_defaults_to_false(db, user):
    svc = Service.objects.create(name="f3-default-svc", owner=user)
    assert svc.use_internal_network is False
    field = Service._meta.get_field("use_internal_network")
    assert field.default is False


def test_f3_deploy_docker_does_not_attach_platform_bridge_by_default(
    db, user, mock_docker_client
):
    svc = Service.objects.create(name="f3-deploy-no-plat", owner=user)
    adapter = object.__new__(LocalAdapter)
    adapter.docker_client = mock_docker_client
    adapter.k8s_client = None
    adapter.batch_v1 = None
    adapter._service_id = svc.id
    adapter.service_id = str(svc.id)

    with patch.object(LocalAdapter, "_wait_container_healthy", return_value=True), patch.object(
        adapter, "_apply_egress_restrictions"
    ), patch.object(
        adapter, "_resolve_network_name", return_value="smsly-net-proj1234"
    ), patch(
        "apps.deployments.services.network_scope.ensure_platform_bridge",
        return_value="smsly-platform-net",
    ) as mock_plat, patch(
        "apps.deployments.services.container_runtime.get_runtime_for_container",
        return_value=None,
    ):
        mock_docker_client.containers.get.side_effect = ns_mod.docker.errors.NotFound("none")
        adapter._deploy_docker(
            name=svc.name,
            image="nginx:alpine",
            env={"PORT": "8000"},
        )

    mock_plat.assert_not_called()
    create_kwargs = mock_docker_client.containers.create.call_args.kwargs
    assert "smsly-platform-net" not in create_kwargs["networking_config"]
    assert list(create_kwargs["networking_config"].keys()) == ["smsly-net-proj1234"]


def test_f3_deploy_docker_attaches_platform_bridge_when_opted_in(
    db, user, mock_docker_client
):
    svc = Service.objects.create(
        name="f3-deploy-optin", owner=user, use_internal_network=True
    )
    adapter = object.__new__(LocalAdapter)
    adapter.docker_client = mock_docker_client
    adapter.k8s_client = None
    adapter.batch_v1 = None
    adapter._service_id = svc.id
    adapter.service_id = str(svc.id)

    with patch.object(LocalAdapter, "_wait_container_healthy", return_value=True), patch.object(
        adapter, "_apply_egress_restrictions"
    ), patch.object(
        adapter, "_resolve_network_name", return_value="smsly-net-proj1234"
    ), patch(
        "apps.deployments.services.network_scope.ensure_platform_bridge",
        return_value="smsly-platform-net",
    ) as mock_plat, patch(
        "apps.deployments.services.container_runtime.get_runtime_for_container",
        return_value=None,
    ):
        mock_docker_client.containers.get.side_effect = ns_mod.docker.errors.NotFound("none")
        adapter._deploy_docker(
            name=svc.name,
            image="nginx:alpine",
            env={"PORT": "8000"},
        )

    mock_plat.assert_called_once()
    create_kwargs = mock_docker_client.containers.create.call_args.kwargs
    assert "smsly-platform-net" in create_kwargs["networking_config"]


def test_f3_promote_container_repair_branch_does_not_attach_platform_bridge_when_disabled(
    db, user, mock_docker_client
):
    svc = Service.objects.create(
        name="f3-promote-no-plat", owner=user, use_internal_network=False
    )
    adapter = object.__new__(LocalAdapter)
    adapter.docker_client = mock_docker_client
    adapter.k8s_client = None
    adapter.batch_v1 = None
    adapter._service_id = svc.id

    green = MagicMock()
    green.id = "green-123"
    green.name = f"{svc.name}-green-abc"
    green.labels = {
        "smsly.blue_green.is_public": "True",
        "smsly.blue_green.port": "8000",
        "smsly.blue_green.host_rule": f"Host(`{svc.name}.example.com`)",
    }
    green.attrs = {
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Config": {"Env": ["PORT=8000"], "Cmd": None, "Entrypoint": None, "Healthcheck": None},
        "HostConfig": {"Binds": None, "RestartPolicy": {}},
    }
    green.image.tags = ["app:v1"]

    promoted = MagicMock()
    promoted.id = "promoted-123"
    promoted.attrs = {"NetworkSettings": {"Networks": {"smsly-net-proj1234": {}}}}
    mock_docker_client.containers.create.return_value = promoted

    def _get_ctr(cid):
        if cid == "green-123":
            return green
        raise ns_mod.docker.errors.NotFound("no old container")

    mock_docker_client.containers.get.side_effect = _get_ctr

    with patch.object(LocalAdapter, "_wait_container_healthy", return_value=True), patch.object(
        adapter, "_apply_egress_restrictions"
    ), patch.object(
        adapter, "_resolve_network_name", return_value="smsly-net-proj1234"
    ), patch(
        "apps.deployments.services.network_scope.ensure_platform_bridge",
        return_value="smsly-platform-net",
    ) as mock_plat:
        adapter.promote_container(svc.name, "green-123")

    mock_plat.assert_not_called()
    create_kwargs = mock_docker_client.containers.create.call_args.kwargs
    assert "smsly-platform-net" not in create_kwargs["networking_config"]


def test_f3_ensure_platform_bridge_disables_icc_and_applies_egress():
    client = MagicMock()
    client.networks.get.side_effect = ns_mod.docker.errors.NotFound("missing")
    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod, "apply_egress_restrictions"
    ) as mock_egress:
        name = ns_mod.ensure_platform_bridge()

    assert name == "smsly-platform-net"
    client.networks.create.assert_called_once()
    create_kw = client.networks.create.call_args.kwargs
    assert create_kw["options"]["com.docker.network.bridge.enable_icc"] == "false"
    mock_egress.assert_called_once_with("smsly-platform-net", ["0.0.0.0/0"])


def test_f3_reconcile_network_isolation_includes_platform_and_build_bridges():
    client = MagicMock()
    plat_net = MagicMock()
    plat_net.name = "smsly-platform-net"
    build_net = MagicMock()
    build_net.name = "smsly-build-net"
    client.networks.list.side_effect = lambda names=None: (
        [plat_net]
        if names == ["smsly-platform-net"]
        else ([build_net] if names == ["smsly-build-net"] else [])
    )
    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod, "_purge_stale_only", return_value=0
    ), patch.object(ns_mod, "refresh_domain_egress", return_value={}), patch.object(
        ns_mod, "_list_docker_user_rules", return_value=[]
    ), patch.object(
        ns_mod, "_get_bridge_interface_name", side_effect=lambda n: f"br-{n[:8]}"
    ), patch.object(ns_mod, "apply_egress_restrictions") as mock_egress, patch.object(
        ns_mod, "apply_build_network_isolation", return_value=True
    ) as mock_build_iso, patch.object(
        ns_mod, "ensure_mesh_reply_rule", return_value=True
    ), patch.object(
        ns_mod, "ensure_backend_mesh_egress", return_value=True
    ), patch.object(ns_mod, "ensure_edge_mesh_egress", return_value=True):
        stats = ns_mod.reconcile_network_isolation()

    mock_egress.assert_called_once_with("smsly-platform-net", ["0.0.0.0/0"])
    mock_build_iso.assert_called_once_with("smsly-build-net")
    assert stats["reapplied"] == 2


# ============================================================================
# F4: L3 promote_container parity & runsc DNS resolution
# ============================================================================


def test_f4_promote_container_preserves_runtime_readonly_tmpfs_ulimits_extra_hosts_dns(
    db, user, mock_docker_client
):
    svc = Service.objects.create(name="f4-promote-parity", owner=user)
    adapter = object.__new__(LocalAdapter)
    adapter.docker_client = mock_docker_client
    adapter.k8s_client = None
    adapter.batch_v1 = None
    adapter._service_id = svc.id

    custom_tmpfs = {"/tmp": "size=64m", "/run": "size=16m"}
    custom_ulimits = [{"Name": "nofile", "Soft": 2048, "Hard": 4096}]
    green = MagicMock()
    green.id = "green-runsc"
    green.name = f"{svc.name}-green-111"
    green.labels = {
        "smsly.blue_green.is_public": "True",
        "smsly.blue_green.port": "8000",
        "smsly.blue_green.host_rule": f"Host(`{svc.name}.example.com`)",
    }
    green.attrs = {
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Config": {"Env": ["PORT=8000"], "Cmd": ["python", "app.py"], "Entrypoint": None, "Healthcheck": None},
        "HostConfig": {
            "Runtime": "runsc",
            "ReadonlyRootfs": True,
            "Tmpfs": custom_tmpfs,
            "Ulimits": custom_ulimits,
            "ExtraHosts": ["postgres-svc:172.30.0.10", "redis-svc:172.30.0.11"],
            "Dns": ["10.100.0.1", "8.8.8.8", "1.1.1.1"],
            "Binds": ["/data:/app/data:rw"],
            "RestartPolicy": {"Name": "unless-stopped"},
        },
    }
    green.image.tags = ["app:runsc"]

    promoted = MagicMock()
    promoted.id = "promoted-runsc"
    promoted.attrs = {"NetworkSettings": {"Networks": {"smsly-net-proj1234": {}}}}
    mock_docker_client.containers.create.return_value = promoted
    mock_docker_client.containers.get.side_effect = lambda cid: (
        green if cid == "green-runsc" else (_ for _ in ()).throw(ns_mod.docker.errors.NotFound("none"))
    )

    with tempfile.TemporaryDirectory() as tmp_builds, patch.dict(
        os.environ, {"SMSLY_BUILDS_DIR": tmp_builds}
    ), patch.object(LocalAdapter, "_wait_container_healthy", return_value=True), patch.object(
        adapter, "_apply_egress_restrictions"
    ), patch.object(
        adapter, "_resolve_network_name", return_value="smsly-net-proj1234"
    ):
        adapter.promote_container(svc.name, "green-runsc")

    kw = mock_docker_client.containers.create.call_args.kwargs
    assert kw["runtime"] == "runsc"
    assert kw["read_only"] is True
    assert kw["tmpfs"] == custom_tmpfs
    assert kw["ulimits"] == custom_ulimits
    assert kw["dns"] == ["10.100.0.1", "8.8.8.8", "1.1.1.1"]
    eh = kw["extra_hosts"]
    eh_map = (
        eh
        if isinstance(eh, dict)
        else dict(item.split(":", 1) for item in eh if ":" in item)
    )
    assert eh_map["postgres-svc"] == "172.30.0.10"
    assert eh_map["redis-svc"] == "172.30.0.11"
    assert any(":/etc/resolv.conf:ro" in str(b) for b in kw["volumes"])


def test_f4_ensure_sandbox_resolv_conf_materializes_and_heals_directory_poison():
    with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
        os.environ, {"SMSLY_BUILDS_DIR": tmp_dir}
    ):
        poison_target = os.path.join(tmp_dir, cr_mod.SANDBOX_RESOLV_CONF_FILENAME)
        os.makedirs(poison_target, exist_ok=True)
        assert os.path.isdir(poison_target)

        resolv_path = cr_mod.ensure_sandbox_resolv_conf(["10.100.0.1", "8.8.8.8", "127.0.0.11"])
        assert resolv_path == poison_target
        assert os.path.isfile(resolv_path)
        content = open(resolv_path, encoding="utf-8").read()
        assert "nameserver 10.100.0.1" in content
        assert "nameserver 8.8.8.8" in content
        assert "127.0.0.11" not in content


def test_f4_network_scope_allows_both_tcp_and_udp_port_53():
    fake_net = MagicMock()
    fake_net.attrs = {"Id": "11112222333344445555666677778888"}
    mock_client = MagicMock()
    mock_client.networks.get.return_value = fake_net
    ok_proc = MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(ns_mod.docker, "from_env", return_value=mock_client), patch.object(
        ns_mod.subprocess, "run", return_value=ok_proc
    ) as mock_run:
        ns_mod.apply_egress_restrictions("smsly-net-dns53", ["0.0.0.0/0"])

    scripts = [c.args[0][-1] for c in mock_run.call_args_list]
    assert any("-p tcp --dport 53 -j RETURN" in s for s in scripts)
    assert any("-p udp --dport 53 -j RETURN" in s for s in scripts)


def test_f4_container_refresh_preserves_readonly_tmpfs_ulimits_and_dns(db, user):
    svc = Service.objects.create(name="f4-refresh-svc", owner=user)
    container = MagicMock()
    container.id = "oldcid1234567890"
    container.name = svc.name
    container.status = "running"
    container.image.tags = ["app:v2"]
    container.attrs = {
        "Config": {"Image": "app:v2", "Labels": {}, "Env": ["PORT=8000"]},
        "HostConfig": {
            "Runtime": "runsc",
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "size=100m"},
            "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
            "Dns": ["8.8.8.8", "1.1.1.1"],
            "ExtraHosts": ["pg:172.30.0.2"],
            "RestartPolicy": {"Name": "unless-stopped"},
        },
        "NetworkSettings": {
            "Networks": {"smsly-net-proj": {"Aliases": [svc.name]}}
        },
        "Mounts": [],
    }
    new_ctr = MagicMock()
    new_ctr.id = "newcid1234567890"
    new_ctr.status = "running"
    client = MagicMock()
    client.containers.get.return_value = container
    client.containers.create.return_value = new_ctr
    client.api.create_endpoint_config.return_value = {}

    with tempfile.TemporaryDirectory() as tmp_builds, patch.dict(
        os.environ, {"SMSLY_BUILDS_DIR": tmp_builds}
    ), patch(
        "apps.cloud.docker_client.get_docker_client",
        return_value=client,
    ), patch(
        "apps.deployments.services.container_refresh._docker_client",
        return_value=client,
    ), patch(
        "apps.deployments.services.container_refresh._resolve_target_container",
        return_value=container,
    ):
        res = recreate_with_fresh_env(svc)

    assert res["ok"] is True
    kw = client.containers.create.call_args.kwargs
    assert kw["runtime"] == "runsc"
    assert kw["read_only"] is True
    assert kw["tmpfs"] == {"/tmp": "size=100m"}
    assert kw["ulimits"] == [{"Name": "nofile", "Soft": 1024, "Hard": 2048}]
    assert kw["dns"] == ["8.8.8.8", "1.1.1.1"]
    assert "/etc/resolv.conf" in [v.get("bind") for v in (kw.get("volumes") or {}).values()]


def test_f3_project_view_create_network_does_not_dual_home_without_optin(db, api_client, user):
    proj = Project.objects.create(name="ProjF3", slug="proj-f3", owner=user)
    Service.objects.create(name="svc-f3-iso", owner=user, project=proj)

    client = MagicMock()
    net = MagicMock()
    client.networks.create.return_value = net
    ctr = MagicMock()
    ctr.attrs = {"State": {"Status": "running"}, "NetworkSettings": {"Networks": {}}}
    client.containers.get.return_value = ctr

    api_client.force_authenticate(user=user)
    with patch("docker.from_env", return_value=client), patch(
        "apps.cloud.docker_client.get_docker_client", return_value=client
    ), patch(
        "apps.deployments.services.network_scope.allocate_project_subnet",
        return_value="172.30.99.0/24",
    ), patch(
        "apps.deployments.services.network_scope.ensure_platform_bridge"
    ) as mock_plat_br, patch(
        "apps.deployments.services.network_scope.attach_container_to_platform_bridge"
    ) as mock_plat_attach:
        resp = api_client.post(f"/api/v1/projects/{proj.id}/internal-network/", {}, format="json")

    assert resp.status_code in (200, 201)
    mock_plat_br.assert_not_called()
    mock_plat_attach.assert_not_called()


def test_f3_project_view_backfill_respects_service_opt_in_and_explicit_dual_platform(
    db, api_client, user
):
    from apps.deployments.models.network_scope import ScopedNetwork
    from django.contrib.contenttypes.models import ContentType

    proj = Project.objects.create(name="ProjF3b", slug="proj-f3b", owner=user)
    ScopedNetwork.objects.create(
        content_type=ContentType.objects.get_for_model(Project),
        object_id=proj.id,
        network_name=f"smsly-net-{str(proj.id).replace('-', '')[:8]}",
        subnet="172.30.100.0/24",
    )
    svc_default = Service.objects.create(name="svc-f3-no", owner=user, project=proj)
    svc_optin = Service.objects.create(
        name="svc-f3-yes", owner=user, project=proj, use_internal_network=True
    )

    client = MagicMock()
    client.networks.get.return_value = MagicMock()
    ctr = MagicMock()
    ctr.id = "cid-opt"
    ctr.attrs = {"State": {"Status": "running"}, "NetworkSettings": {"Networks": {}}}
    client.containers.get.return_value = ctr

    api_client.force_authenticate(user=user)
    with patch("docker.from_env", return_value=client), patch(
        "apps.cloud.docker_client.get_docker_client", return_value=client
    ), patch(
        "apps.deployments.services.network_scope.ensure_platform_bridge",
        return_value="smsly-platform-net",
    ), patch(
        "apps.deployments.services.network_scope.attach_container_to_platform_bridge",
        return_value=True,
    ) as mock_plat_attach:
        resp = api_client.post(f"/api/v1/projects/{proj.id}/internal-network/", {}, format="json")

    assert resp.status_code == 200
    attached_names = [c.args[1] for c in mock_plat_attach.call_args_list]
    assert svc_optin.name in attached_names
    assert svc_default.name not in attached_names


def test_f3_service_restore_requests_dual_home_only_when_service_opted_in():
    from apps.deployments.services.transfer_service.mixins.service_restore import (
        SingleServiceRestoreMixin,
    )

    class _DummyRestore(SingleServiceRestoreMixin):
        def __init__(self, use_internal):
            self.transfer = MagicMock()
            self.transfer.service = SimpleNamespace(
                id="sid",
                name="rest-svc",
                project=None,
                internal_port=8000,
                public_domain="rest.example.com",
                owner=SimpleNamespace(email="u@e.com"),
                use_internal_network=use_internal,
            )
            self.transfer.source_backup = MagicMock(
                metadata={"docker_image": "app:1", "env_vars": []}
            )
            self.sent = None

        def _target_server_record(self):
            return SimpleNamespace(is_lite_agent=False)

        def _update(self, pct, msg):
            return None

        def _build_restore_trigger_script(self, owner_email, remote_backup_path):
            return "echo ok"

        def _remap_target_platform_env(self):
            return None

        def _detect_transferred_health_path(self, name, port):
            return "/health"

        def _node_api_request(self, path, body=None, timeout=60):
            if path == "incoming/deploy":
                self.sent = body
            return {"exit_code": 0, "stdout": ""}

        def _seed_target_deployment_record(self, metadata, container_id=None):
            return None

    with patch("apps.deployments.models.network_scope.ScopedNetwork"):
        r_off = _DummyRestore(False)
        r_off._restore_single_service("/tmp/backup-off.tar.gz")
        assert "dual_home" not in r_off.sent

        r_on = _DummyRestore(True)
        r_on._restore_single_service("/tmp/backup-on.tar.gz")
        assert r_on.sent.get("dual_home") is True


def test_f4_promote_container_defaults_sandbox_dns_when_green_host_config_omits_dns(
    db, user, mock_docker_client
):
    svc = Service.objects.create(name="f4-default-dns", owner=user)
    adapter = object.__new__(LocalAdapter)
    adapter.docker_client = mock_docker_client
    adapter.k8s_client = None
    adapter.batch_v1 = None
    adapter._service_id = svc.id

    green = MagicMock()
    green.id = "green-nodns"
    green.name = "f4-default-dns-green-1"
    green.labels = {}
    green.attrs = {
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Config": {"Env": ["PORT=8000"], "Cmd": None, "Entrypoint": None, "Healthcheck": None},
        "HostConfig": {"Runtime": "runsc"},
    }
    green.image.tags = ["app:v1"]
    promoted = MagicMock()
    promoted.id = "prom-nodns"
    promoted.attrs = {"NetworkSettings": {"Networks": {"smsly-net": {}}}}
    mock_docker_client.containers.create.return_value = promoted
    mock_docker_client.containers.get.side_effect = lambda cid: (
        green if cid == "green-nodns" else (_ for _ in ()).throw(ns_mod.docker.errors.NotFound("x"))
    )

    with tempfile.TemporaryDirectory() as tmp_builds, patch.dict(
        os.environ, {"SMSLY_BUILDS_DIR": tmp_builds}
    ), patch(
        "apps.deployments.services.container_runtime.sandbox_dns_servers",
        return_value=["8.8.8.8", "1.1.1.1"],
    ), patch.object(LocalAdapter, "_wait_container_healthy", return_value=True), patch.object(
        adapter, "_apply_egress_restrictions"
    ), patch.object(
        adapter, "_resolve_network_name", return_value="smsly-net"
    ):
        adapter.promote_container(svc.name, "green-nodns")

    kw = mock_docker_client.containers.create.call_args.kwargs
    assert kw["dns"] == ["8.8.8.8", "1.1.1.1"]
    assert kw["read_only"] is True
    assert any(":/etc/resolv.conf:ro" in str(b) for b in kw["volumes"])


def test_f4_ensure_sandbox_resolv_conf_returns_none_when_builds_dir_missing():
    with patch.dict(os.environ, {"SMSLY_BUILDS_DIR": "/nonexistent/smsly-builds-path-xyz"}):
        assert cr_mod.ensure_sandbox_resolv_conf(["8.8.8.8"]) is None


def test_f4_container_refresh_escape_hatch_force_default_runtime_drops_runsc_and_dns(
    db, user
):
    svc = Service.objects.create(name="f4-escape-svc", owner=user)
    container = MagicMock()
    container.id = "oldescape12345"
    container.name = svc.name
    container.status = "running"
    container.image.tags = ["app:v1"]
    container.attrs = {
        "Config": {"Image": "app:v1", "Labels": {}, "Env": ["PORT=8000"]},
        "HostConfig": {
            "Runtime": "runsc",
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "size=50m"},
            "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
            "RestartPolicy": {"Name": "unless-stopped"},
        },
        "NetworkSettings": {"Networks": {"smsly-net": {"Aliases": [svc.name]}}},
        "Mounts": [],
    }
    new_ctr = MagicMock()
    new_ctr.id = "newescape12345"
    new_ctr.status = "running"
    client = MagicMock()
    client.containers.create.return_value = new_ctr
    client.api.create_endpoint_config.return_value = {}

    with patch(
        "apps.cloud.docker_client.get_docker_client", return_value=client
    ), patch(
        "apps.deployments.services.container_refresh._docker_client", return_value=client
    ), patch(
        "apps.deployments.services.container_refresh._resolve_target_container",
        return_value=container,
    ):
        res = recreate_with_fresh_env(svc, force_default_runtime=True)

    assert res["ok"] is True
    kw = client.containers.create.call_args.kwargs
    assert "runtime" not in kw
    assert "extra_hosts" not in kw
    assert kw["read_only"] is True


@pytest.mark.parametrize(
    "runtime_name,expected",
    [
        ("runsc", True),
        ("kata-runtime", True),
        ("kata", False),
        ("runc", False),
        (None, False),
    ],
)
def test_f4_is_sandboxed_runtime_matrix(runtime_name, expected):
    assert cr_mod.is_sandboxed_runtime(runtime_name) is expected


@pytest.mark.parametrize(
    "blocked_cidr",
    [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "169.254.169.254/32",
    ],
)
def test_f3_build_network_blocks_every_private_and_metadata_cidr(blocked_cidr):
    fake_net = MagicMock()
    fake_net.attrs = {"Id": "buildnetabcdef1234567890"}
    client = MagicMock()
    client.networks.get.return_value = fake_net
    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod.subprocess, "run", return_value=MagicMock(returncode=0, stdout="", stderr="")
    ) as mrun:
        assert ns_mod.apply_build_network_isolation("smsly-build-net") is True
    joined = "\n".join(c.args[0][-1] for c in mrun.call_args_list)
    assert f"-d {blocked_cidr} -j DROP" in joined

