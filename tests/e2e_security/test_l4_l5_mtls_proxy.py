"""E2E security tests for L4 (Worker node mTLS fail-closed) & L5 (`socket-proxy` isolation)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.services.remote_orchestrator.deployment import DeploymentMixin
from apps.deployments.services.spawning_service import SpawningService
from apps.deployments.tasks.deploy import deploy_container as dc
from apps.mtls.models import MtlsConfig
from apps.mtls.services.envoy_sidecar import EnvoySidecar
from tests.e2e_security.helpers import load_compose_yaml

pytestmark = [pytest.mark.security]


# ============================================================================
# F5: L4 Worker node mTLS fail-closed & sidecar image/config wiring
# ============================================================================


def test_f5_worker_compose_configures_envoy_sidecar_image_and_build_context():
    node_compose = load_compose_yaml("infrastructure/docker/docker-compose.node.yml")
    services = node_compose["services"]
    for svc_name in ("backend", "celery-worker"):
        svc = services[svc_name]
        env = svc.get("environment") or {}
        assert "ENVOY_SIDECAR_IMAGE" in env
        assert "registry:5000/smsly/envoy-spire-sidecar" in str(env["ENVOY_SIDECAR_IMAGE"])
        vols = [str(v) for v in (svc.get("volumes") or [])]
        assert any(":/opt/envoy-build:ro" in v for v in vols), (
            f"{svc_name} in docker-compose.node.yml must mount /opt/envoy-build"
        )
    volumes_top = node_compose.get("volumes") or {}
    assert "spire-ecosystem-agent-socket" in volumes_top
    assert "spire-ecosystem-agent-svids" in volumes_top


def test_f5_resolve_sidecar_image_rewrites_to_master_mesh_registry_on_worker():
    with patch.dict(
        os.environ,
        {
            "MASTER_MESH_IP": "10.100.0.1",
            "ENVOY_SIDECAR_IMAGE": "registry:5000/smsly/envoy-spire-sidecar:latest",
        },
        clear=False,
    ):
        resolved = EnvoySidecar.resolve_sidecar_image()
    assert resolved == "10.100.0.1:5000/smsly/envoy-spire-sidecar:latest"


def test_f5_resolve_sidecar_image_rewrites_ghcr_default_on_worker():
    with patch.dict(
        os.environ,
        {
            "MASTER_MESH_IP": "10.100.0.1",
            "ENVOY_SIDECAR_IMAGE": "ghcr.io/smsly/envoy-spire-sidecar:latest",
        },
        clear=False,
    ):
        resolved = EnvoySidecar.resolve_sidecar_image("ghcr.io/smsly/envoy-spire-sidecar:latest")
    assert resolved == "10.100.0.1:5000/smsly/envoy-spire-sidecar:latest"


def test_f5_deploy_container_fails_closed_when_sidecar_image_unavailable(db, user):
    provider = CloudProvider.objects.create(
        name="f5-local", provider_type=CloudProvider.ProviderType.LOCAL, is_active=True
    )
    svc = Service.objects.create(
        name="f5-mtls-svc", owner=user, provider=provider, managed_by="USER"
    )
    cfg = MtlsConfig.objects.get(service=svc)
    cfg.enabled = True
    cfg.sidecar_enabled = True
    cfg.save(update_fields=["enabled", "sidecar_enabled"])
    dep = Deployment.objects.create(
        service=svc, status=Deployment.Status.BUILDING, commit_hash="abc123"
    )

    compute = MagicMock()
    compute.deploy_container.return_value = MagicMock(resource_id="cid123")
    with patch.object(dc, "ComputeService", return_value=compute), patch(
        "apps.deployments.tasks.deploy.health._local_container_timeout_seconds",
        return_value=1,
    ), patch(
        "apps.deployments.tasks.deploy.health._wait_for_local_container_healthy",
        return_value=True,
    ), patch.object(
        EnvoySidecar,
        "remount_if_stale",
        side_effect=docker.errors.ImageNotFound("403 Forbidden from ghcr.io"),
    ):
        with pytest.raises(RuntimeError, match="mTLS sidecar injection failed"):
            dc._deploy_container(dep, provider, "app:v1")


def test_f5_deploy_container_fails_closed_when_sidecar_not_ready(db, user):
    provider = CloudProvider.objects.create(
        name="f5-local-2", provider_type=CloudProvider.ProviderType.LOCAL, is_active=True
    )
    svc = Service.objects.create(
        name="f5-mtls-notready", owner=user, provider=provider, managed_by="USER"
    )
    cfg = MtlsConfig.objects.get(service=svc)
    cfg.enabled = True
    cfg.sidecar_enabled = True
    cfg.save(update_fields=["enabled", "sidecar_enabled"])
    dep = Deployment.objects.create(
        service=svc, status=Deployment.Status.BUILDING, commit_hash="def456"
    )

    compute = MagicMock()
    compute.deploy_container.return_value = MagicMock(resource_id="cid456")
    with patch.object(dc, "ComputeService", return_value=compute), patch(
        "apps.deployments.tasks.deploy.health._local_container_timeout_seconds",
        return_value=1,
    ), patch(
        "apps.deployments.tasks.deploy.health._wait_for_local_container_healthy",
        return_value=True,
    ), patch.object(EnvoySidecar, "remount_if_stale", return_value={"status": "injected"}), patch.object(
        EnvoySidecar, "wait_sidecar_ready", return_value=False
    ):
        with pytest.raises(RuntimeError, match="did not become ready"):
            dc._deploy_container(dep, provider, "app:v1")


def test_f5_spawning_service_fails_closed_when_sidecar_inject_raises(db, user):
    svc = Service.objects.create(name="f5-replica-mtls", owner=user)
    cfg = MtlsConfig.objects.get(service=svc)
    cfg.enabled = True
    cfg.sidecar_enabled = True
    cfg.save(update_fields=["enabled", "sidecar_enabled"])

    spawner = SpawningService()
    with patch.object(
        EnvoySidecar,
        "inject_sidecar",
        side_effect=docker.errors.ImageNotFound("sidecar missing"),
    ):
        with pytest.raises(RuntimeError, match="Envoy sidecar injection failed"):
            spawner._maybe_inject_sidecar(svc, MagicMock(), MagicMock())


def test_f5_remote_orchestrator_ships_mtls_payload_and_preregisters_spire(db, user):
    svc = Service.objects.create(name="f5-remote-mtls", owner=user)
    cfg = MtlsConfig.objects.get(service=svc)
    cfg.enabled = True
    cfg.sidecar_enabled = True
    cfg.save(update_fields=["enabled", "sidecar_enabled"])
    dep = Deployment.objects.create(
        service=svc, status=Deployment.Status.QUEUED, commit_hash="head1"
    )

    class _DummyClient(DeploymentMixin):
        def __init__(self):
            self.sent_payload = None

        def _request(self, method, path, payload=None, timeout=60):
            self.sent_payload = payload
            resp = MagicMock()
            resp.status_code = 200
            return resp

        def _parse_json_response(self, resp, ctx):
            return {"deployment_id": "remote-dep-1"}

    client = _DummyClient()
    with patch(
        "apps.deployments.tasks_spiffe._create_spire_entry", return_value=True
    ) as mock_spire, patch(
        "apps.deployments.tasks_spiffe._live_ecosystem_agent_id",
        return_value="spiffe://ecosystem.local/agent/node1",
    ):
        client.trigger_deploy(dep, "remote-svc-1", image_name="registry:5000/app:1")

    assert client.sent_payload is not None
    assert client.sent_payload["mtls"]["enabled"] is True
    assert client.sent_payload["mtls"]["sidecar_enabled"] is True
    mock_spire.assert_called_once()


# ============================================================================
# F6: L5 socket-proxy network & role isolation
# ============================================================================


def test_f6_prod_socket_proxy_removed_from_smsly_net():
    prod = load_compose_yaml("docker-compose.prod.yml")
    sp = prod["services"]["socket-proxy"]
    sp_nets = sp.get("networks") or []
    assert "smsly-net" not in sp_nets
    assert sp_nets == ["socket-proxy"]
    assert prod["networks"]["socket-proxy"]["internal"] is True
    assert "socket-proxy" in (prod["services"]["backend"].get("networks") or [])


def test_f6_node_compose_splits_readonly_traefik_socket_proxy():
    node = load_compose_yaml("infrastructure/docker/docker-compose.node.yml")
    sp = node["services"]["socket-proxy"]
    assert sp.get("networks") == ["socket-proxy"]
    assert "traefik-proxy-net" not in (sp.get("networks") or [])

    tsp = node["services"]["traefik-socket-proxy"]
    tsp_env = tsp["environment"]
    assert tsp_env["CONTAINERS"] == 1
    assert tsp_env["NETWORKS"] == 1
    assert tsp_env.get("POST", 0) == 0
    assert tsp_env.get("EXEC", 0) == 0
    assert tsp_env.get("BUILD", 0) == 0
    assert tsp_env.get("VOLUMES", 0) == 0
    assert tsp["networks"] == ["traefik-proxy-net"]

    traefik_cmd = " ".join(node["services"]["traefik"]["command"])
    assert "tcp://traefik-socket-proxy:2375" in traefik_cmd
    assert "tcp://socket-proxy:2375" not in traefik_cmd


def test_f6_agent_lite_splits_readonly_traefik_socket_proxy():
    lite = load_compose_yaml("infrastructure/docker/docker-compose.agent-lite.yml")
    assert "traefik-socket-proxy" in lite["services"]
    tsp = lite["services"]["traefik-socket-proxy"]
    assert tsp["environment"].get("POST", 0) == 0
    assert tsp["environment"].get("EXEC", 0) == 0
    assert tsp["networks"] == ["traefik-proxy-net"]
    assert lite["networks"]["traefik-proxy-net"]["internal"] is True

    traefik = lite["services"]["traefik"]
    assert "tcp://traefik-socket-proxy:2375" in " ".join(traefik["command"])
    assert "socket-proxy" not in (traefik.get("networks") or [])


def test_f6_standalone_socket_proxy_uses_internal_network():
    standalone = load_compose_yaml("infrastructure/docker/docker-compose.socket-proxy.yml")
    sp = standalone["services"]["socket-proxy"]
    assert sp["networks"] == ["socket-proxy"]
    assert standalone["networks"]["socket-proxy"]["internal"] is True
    assert sp["environment"]["SECRETS"] == 0
    assert sp["environment"]["SWARM"] == 0
    assert sp["environment"]["PLUGINS"] == 0


def test_f5_ecosystem_service_auto_enforces_mtls_even_when_row_disabled(db, user):
    provider = CloudProvider.objects.create(
        name="f5-eco-prov", provider_type=CloudProvider.ProviderType.LOCAL, is_active=True
    )
    svc = Service.objects.create(
        name="f5-eco-svc", owner=user, provider=provider, managed_by="ECOSYSTEM"
    )
    dep = Deployment.objects.create(
        service=svc, status=Deployment.Status.BUILDING, commit_hash="eco1"
    )
    # Remove any signal-seeded row to verify ECOSYSTEM auto-provisions enabled=True
    # and fails closed when sidecar injection fails (without socket fallback hangs).
    MtlsConfig.objects.filter(service=svc).delete()
    compute = MagicMock()
    compute.deploy_container.return_value = MagicMock(resource_id="cid-eco")
    with patch("apps.cloud.docker_client.get_docker_client", return_value=MagicMock()), patch.object(
        dc, "ComputeService", return_value=compute
    ), patch(
        "apps.deployments.tasks.deploy.health._local_container_timeout_seconds", return_value=1
    ), patch(
        "apps.deployments.tasks.deploy.health._wait_for_local_container_healthy",
        return_value=True,
    ), patch.object(
        EnvoySidecar, "remount_if_stale", side_effect=RuntimeError("no image")
    ):
        with pytest.raises(RuntimeError, match="mTLS sidecar injection failed"):
            dc._deploy_container(dep, provider, "app:eco")
    cfg = MtlsConfig.objects.get(service=svc)
    assert cfg.enabled is True
    assert cfg.sidecar_enabled is True


def test_f5_compose_override_fails_closed_for_ecosystem_mtls(db, user, tmp_path):
    from apps.deployments.services.pipeline.compose_networking import ComposeNetworkingMixin
    from apps.deployments.services.pipeline.exceptions import BuildError

    svc = Service.objects.create(
        name="f5-eco-comp",
        owner=user,
        compose_file="docker-compose.yml",
        managed_by="ECOSYSTEM",
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  f5-eco-comp:\n    image: alpine\n", encoding="utf-8"
    )

    class _H(ComposeNetworkingMixin):
        build_dir = str(tmp_path)
        source_dir = str(tmp_path)
        deployment = MagicMock(id="dep-eco-comp")
        service = svc

        def _resolve_service_network_name(self):
            return "smsly-net-eco"

        def _compose_traefik_labels(self, _):
            return {}

    with patch.object(
        EnvoySidecar, "inject_sidecar_compose", side_effect=RuntimeError("pull 403")
    ):
        with pytest.raises(BuildError, match="Envoy sidecar compose injection failed"):
            _H()._write_compose_routing_override("f5-eco-comp", "proj-eco")


def test_f5_inject_sidecar_compose_uses_resolved_image_on_worker(db, user):
    svc = Service.objects.create(name="f5-comp-img", owner=user)
    cfg = MtlsConfig.objects.get(service=svc)
    cfg.enabled = True
    cfg.sidecar_enabled = True
    cfg.save(update_fields=["enabled", "sidecar_enabled"])
    svc.refresh_from_db()

    with patch.dict(
        os.environ,
        {
            "MASTER_MESH_IP": "10.100.0.1",
            "ENVOY_SIDECAR_IMAGE": "registry:5000/smsly/envoy-spire-sidecar:latest",
        },
    ), patch(
        "apps.deployments.services.mtls_integration.resolve_spire_volume_name",
        side_effect=lambda v: v,
    ):
        out = EnvoySidecar.inject_sidecar_compose(svc, {})
    sidecar = out["services"][EnvoySidecar.get_sidecar_name(svc)]
    assert sidecar["image"] == "10.100.0.1:5000/smsly/envoy-spire-sidecar:latest"


def test_f6_prod_socket_proxy_disables_swarm_secrets_nodes_services_tasks_configs():
    prod = load_compose_yaml("docker-compose.prod.yml")
    env = prod["services"]["socket-proxy"]["environment"]
    for zero_key in ("SECRETS", "SWARM", "PLUGINS", "NODES", "SERVICES", "TASKS", "CONFIGS"):
        assert env.get(zero_key) == 0, f"{zero_key} must be 0 on socket-proxy"
    # Celery deploy/workers remain connected via internal socket-proxy network
    for svc_name in ("backend", "celery", "celery-fast", "celery-deploy"):
        assert "socket-proxy" in prod["services"][svc_name]["networks"]


def test_f6_prod_spire_agents_can_reach_readonly_traefik_socket_proxy():
    prod = load_compose_yaml("docker-compose.prod.yml")
    for agent_name in ("spire-agent", "spire-agent-ecosystem"):
        agent = prod["services"][agent_name]
        assert "tcp://traefik-socket-proxy:2375" in str(agent["environment"]["DOCKER_HOST"])
        assert "traefik-proxy-net" in agent["networks"]
        assert "socket-proxy" not in agent["networks"]


def test_f6_node_and_agent_lite_disable_dangerous_socket_proxy_flags():
    node = load_compose_yaml("infrastructure/docker/docker-compose.node.yml")
    lite = load_compose_yaml("infrastructure/docker/docker-compose.agent-lite.yml")
    for doc in (node, lite):
        env = doc["services"]["socket-proxy"]["environment"]
        assert env.get("SECRETS") == 0
        assert env.get("SWARM") == 0
        assert env.get("PLUGINS") == 0
        assert doc["networks"]["traefik-proxy-net"]["internal"] is True


@pytest.mark.parametrize(
    "raw_image,mesh_ip,expected_image",
    [
        ("registry:5000/smsly/envoy-spire-sidecar:latest", "10.100.0.1", "10.100.0.1:5000/smsly/envoy-spire-sidecar:latest"),
        ("127.0.0.1:5000/smsly/envoy-spire-sidecar:v2", "10.100.0.1", "10.100.0.1:5000/smsly/envoy-spire-sidecar:v2"),
        ("localhost:5000/smsly/envoy-spire-sidecar:v2", "10.100.0.1", "10.100.0.1:5000/smsly/envoy-spire-sidecar:v2"),
        ("ghcr.io/smsly/envoy-spire-sidecar:latest", "10.100.0.1", "10.100.0.1:5000/smsly/envoy-spire-sidecar:latest"),
        ("registry:5000/smsly/envoy-spire-sidecar:latest", "", "registry:5000/smsly/envoy-spire-sidecar:latest"),
    ],
)
def test_f5_resolve_sidecar_image_matrix(raw_image, mesh_ip, expected_image):
    with patch.dict(os.environ, {"MASTER_MESH_IP": mesh_ip}, clear=False):
        assert EnvoySidecar.resolve_sidecar_image(raw_image) == expected_image


@pytest.mark.parametrize(
    "compose_rel",
    [
        "docker-compose.prod.yml",
        "infrastructure/docker/docker-compose.node.yml",
        "infrastructure/docker/docker-compose.agent-lite.yml",
        "infrastructure/docker/docker-compose.socket-proxy.yml",
    ],
)
@pytest.mark.parametrize("blocked_api", ["SECRETS", "SWARM", "PLUGINS"])
def test_f6_all_socket_proxies_disable_high_risk_apis(compose_rel, blocked_api):
    doc = load_compose_yaml(compose_rel)
    sp = doc["services"]["socket-proxy"]
    assert sp["environment"].get(blocked_api) == 0

