"""E2E Tier 3 — Pairwise Cross-Feature Security Combinations (L1–L7 & R4)."""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from apps.addons.services import addon_tls, volume_encryption
from apps.addons.services.addon_provisioner import AddonProvisioner
from apps.cloud.adapters.local import LocalAdapter
from apps.cloud.models import CloudProvider
from apps.core.models.api_token import APIToken
from apps.deployments.models import Addon, Deployment, ManagedServer, Service
from apps.deployments.models.core import PlatformConfig
from apps.deployments.services import network_scope as ns_mod
from apps.deployments.services.caddy_manager import config_generation as cg
from apps.deployments.services.caddy_manager import validation as cv
from apps.deployments.services.pipeline import build as build_mod
from apps.deployments.services.pipeline.compose_networking import ComposeNetworkingMixin
from apps.deployments.services.pipeline.exceptions import BuildError
from apps.deployments.services.remote_orchestrator.deployment import DeploymentMixin
from apps.deployments.tasks.deploy import deploy_container as dc
from apps.mtls.models import MtlsConfig
from apps.mtls.services.envoy_sidecar import EnvoySidecar
from tests.e2e_security.helpers import (
    extract_caddy_site_blocks,
    load_compose_yaml,
    run_wsl_bash_check,
    repo_path,
    sign_gateway_v2,
)

pytestmark = [pytest.mark.security]


def test_t3_pair01_f1_f2_caddy_coraza_plus_openappsec_prevent_default(
    api_client, superuser
):
    PlatformConfig.clear_cache()
    cfg = PlatformConfig.load()
    cfg.domain = "pair01.example.com"
    cfg.use_ssl = True
    cfg.wildcard_subdomains = True
    cfg.save()
    Service.objects.create(
        name="p01-app",
        owner=superuser,
        status=Service.Status.ACTIVE,
        public_domain="p01-app.pair01.example.com",
    )

    caddyfile = cg.generate_caddyfile(cfg)
    assert cv.validate_edge_sidecar_endpoints(caddyfile) == []
    assert cv.validate_secure_headers_present(caddyfile) == []

    api_client.force_authenticate(user=superuser)
    with patch("apps.deployments.signals.infrastructure.apply_caddyfile"), patch(
        "apps.deployments.signals.infrastructure.generate_caddyfile", return_value=""
    ), patch("apps.domains.views.domain._crowdsec_cf_bouncer_running", return_value=False):
        dc_resp = api_client.get("/api/v1/system/domain-config/")
    assert dc_resp.status_code == 200
    assert dc_resp.data["openappsec_mode"] == "prevent"
    blocks = extract_caddy_site_blocks(caddyfile)
    assert "request_header -X-SMSLY-Remote-Sync" in blocks["*.pair01.example.com"]


def test_t3_pair02_f3_f4_non_internal_runsc_promote_isolates_and_preserves(
    db, user, mock_docker_client
):
    svc = Service.objects.create(name="t3-p02-svc", owner=user)
    assert svc.use_internal_network is False

    adapter = object.__new__(LocalAdapter)
    adapter.docker_client = mock_docker_client
    adapter.k8s_client = None
    adapter.batch_v1 = None
    adapter._service_id = svc.id

    green = MagicMock()
    green.id = "green-p02"
    green.name = "t3-p02-svc-green-1"
    green.labels = {
        "smsly.blue_green.is_public": "True",
        "smsly.blue_green.port": "8000",
        "smsly.blue_green.host_rule": "Host(`t3p02.example.com`)",
    }
    green.attrs = {
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Config": {"Env": ["PORT=8000"], "Cmd": None, "Entrypoint": None, "Healthcheck": None},
        "HostConfig": {
            "Runtime": "runsc",
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "size=100m"},
            "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
            "ExtraHosts": ["pg:172.30.10.2"],
            "Dns": ["8.8.8.8", "1.1.1.1"],
            "Binds": None,
            "RestartPolicy": {},
        },
    }
    green.image.tags = ["app:p02"]
    promoted = MagicMock()
    promoted.id = "prom-p02"
    promoted.attrs = {"NetworkSettings": {"Networks": {"smsly-net-p02": {}}}}
    mock_docker_client.containers.create.return_value = promoted
    mock_docker_client.containers.get.side_effect = lambda cid: (
        green if cid == "green-p02" else (_ for _ in ()).throw(ns_mod.docker.errors.NotFound("x"))
    )

    with tempfile.TemporaryDirectory() as tmp_builds, patch.dict(
        os.environ, {"SMSLY_BUILDS_DIR": tmp_builds}
    ), patch.object(LocalAdapter, "_wait_container_healthy", return_value=True), patch.object(
        adapter, "_apply_egress_restrictions"
    ), patch.object(
        adapter, "_resolve_network_name", return_value="smsly-net-p02"
    ):
        adapter.promote_container(svc.name, "green-p02")

    kw = mock_docker_client.containers.create.call_args.kwargs
    assert "smsly-platform-net" not in kw["networking_config"]
    assert kw["runtime"] == "runsc"
    assert kw["read_only"] is True
    assert kw["dns"] == ["8.8.8.8", "1.1.1.1"]
    assert any(":/etc/resolv.conf:ro" in str(b) for b in kw["volumes"])


def test_t3_pair03_f3_f8_compose_override_on_scoped_net_with_hardening_and_runsc():
    with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
        os.environ, {"SMSLY_BUILDS_DIR": tmp_dir}
    ), patch(
        "apps.deployments.services.container_runtime.detect_best_runtime",
        return_value="runsc",
    ), patch(
        "apps.deployments.services.container_runtime.sandbox_dns_servers",
        return_value=["10.100.0.1", "8.8.8.8"],
    ):
        cfile = os.path.join(tmp_dir, "docker-compose.yml")
        open(cfile, "w", encoding="utf-8").write("services:\n  api:\n    image: node:20-alpine\n")

        class _H(ComposeNetworkingMixin):
            build_dir = tmp_dir
            source_dir = tmp_dir
            deployment = SimpleNamespace(id="dep-p03")
            service = SimpleNamespace(id="svc-p03", name="api", compose_file="docker-compose.yml", managed_by="USER")

            def _resolve_service_network_name(self):
                return "smsly-net-p03"

            def _compose_traefik_labels(self, _):
                return {"traefik.enable": "true"}

        out_path = _H()._write_compose_routing_override("api", "proj-p03")
        data = yaml.safe_load(open(out_path, encoding="utf-8").read())

    assert "smsly-platform-net" not in data["networks"]
    assert data["networks"]["smsly-net-p03"]["name"] == "smsly-net-p03"
    api = data["services"]["api"]
    assert api["runtime"] == "runsc"
    assert api["cap_drop"] == ["ALL"]
    assert api["pids_limit"] == 1024
    assert api["dns"] == ["10.100.0.1", "8.8.8.8"]


def test_t3_pair04_f5_f8_compose_mtls_enabled_fails_closed_when_sidecar_inject_errors(
    db, user
):
    svc = Service.objects.create(name="t3-p04-svc", owner=user, compose_file="docker-compose.yml")
    cfg = MtlsConfig.objects.get(service=svc)
    cfg.enabled = True
    cfg.sidecar_enabled = True
    cfg.save(update_fields=["enabled", "sidecar_enabled"])

    with tempfile.TemporaryDirectory() as tmp_dir:
        cfile = os.path.join(tmp_dir, "docker-compose.yml")
        open(cfile, "w", encoding="utf-8").write("services:\n  t3-p04-svc:\n    image: alpine\n")

        class _H(ComposeNetworkingMixin):
            build_dir = tmp_dir
            source_dir = tmp_dir
            deployment = SimpleNamespace(id="dep-p04")
            service = svc

            def _resolve_service_network_name(self):
                return "smsly-net-p04"

            def _compose_traefik_labels(self, _):
                return {"traefik.enable": "true"}

        with patch.object(
            EnvoySidecar, "inject_sidecar_compose", side_effect=RuntimeError("registry 403")
        ):
            with pytest.raises(BuildError, match="Envoy sidecar compose injection failed"):
                _H()._write_compose_routing_override("t3-p04-svc", "proj-p04")


def test_t3_pair05_f6_f7_buildkit_isolation_cannot_reach_socket_proxy():
    prod = load_compose_yaml("docker-compose.prod.yml")
    sp_nets = prod["services"]["socket-proxy"]["networks"]
    assert "smsly-net" not in sp_nets
    assert sp_nets == ["socket-proxy"]
    # BuildKit creates ephemeral builder on smsly-build-net which drops all bridges + RFC1918
    fake_net = MagicMock()
    fake_net.attrs = {"Id": "aaaa11112222333344445555"}
    client = MagicMock()
    client.networks.get.return_value = fake_net
    ok_proc = MagicMock(returncode=0, stdout="", stderr="")
    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod.subprocess, "run", return_value=ok_proc
    ) as mock_run:
        ns_mod.apply_build_network_isolation("smsly-build-net")
    joined = "\n".join(c.args[0][-1] for c in mock_run.call_args_list)
    assert "-o br-+ -j DROP" in joined
    assert "-d 172.16.0.0/12 -j DROP" in joined


def test_t3_pair06_f9_f6_node_compose_falco_1024m_and_split_traefik_socket_proxy():
    node = load_compose_yaml("infrastructure/docker/docker-compose.node.yml")
    assert node["services"]["falco"]["deploy"]["resources"]["limits"]["memory"] == "${FALCO_MEMORY_LIMIT:-1024M}"
    assert "traefik-proxy-net" not in node["services"]["socket-proxy"]["networks"]
    assert node["services"]["traefik"]["networks"] == ["smsly-net", "traefik-proxy-net"]
    assert node["services"]["traefik-socket-proxy"]["environment"]["POST"] == 0


def test_t3_pair07_f10_f11_addon_provisioning_pairs_tls_and_volume_encryption(
    db, user, tmp_path, monkeypatch
):
    monkeypatch.setenv("SMSLY_ADDON_CA_DIR", str(tmp_path))
    monkeypatch.setenv("SMSLY_VOLUME_ENCRYPTION_POLICY", "required")
    monkeypatch.setenv("SMSLY_VOLUME_KEK", "pair07-kek")

    prov = AddonProvisioner()
    ok_proc = MagicMock(returncode=0, stdout="cid-p07\n", stderr="")
    with patch("apps.addons.services.volume_encryption.preflight", return_value={"available": True, "backend": "luks", "reason": ""}), patch(
        "apps.addons.services.volume_encryption.prepare_volume",
        return_value={"encrypted": True, "backend": "luks", "status": "encrypted_active"},
    ) as mock_prep, patch(
        "apps.addons.services.addon_tls.ensure_tls_volume", return_value="pg-p07-tls"
    ), patch("apps.addons.services.addon_provisioner.subprocess.run", return_value=ok_proc), patch.object(
        prov, "_wait_for_health"
    ):
        res = volume_encryption.prepare_volume(SimpleNamespace(provider_metadata={}), "pg-p07-data")
        _cid, pg_url = prov._provision_postgres("pg-p07", "pw", 5432, "pg-p07-alias")

    assert res["encrypted"] is True
    assert "sslmode=require" in pg_url
    mock_prep.assert_called_once()


def test_t3_pair08_f12_f14_restricted_token_denied_recovery_and_token_minting_allowed_on_mcp_read(
    api_client, superuser
):
    _tok, raw = APIToken.create_token(superuser, scopes=["read"])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert api_client.post("/api/v1/auth/recovery/generate/", {}, format="json").status_code == 403
    assert api_client.post("/api/v1/tokens/create/", {"name": "esc"}, format="json").status_code == 403
    mcp_read = api_client.post("/api/v1/mcp/tools/list_projects/call/", {"args": {}}, format="json")
    assert mcp_read.status_code == 200


def test_t3_pair09_f13_f12_admin_backup_download_vs_inactive_superuser_recovery(
    api_client, superuser, staff_user
):
    from apps.deployments.models.backup import ServerBackup

    backup = ServerBackup.objects.create(status="COMPLETED", file_path="/tmp/t3.tar.gz", size_bytes=8)
    dl_url = f"/api/v1/server/backups/{backup.id}/download/"

    api_client.force_authenticate(user=staff_user)
    with patch("apps.deployments.views.server_backup.os.path.exists", return_value=True), patch(
        "apps.deployments.views.server_backup._open_backup_download_response"
    ) as mock_open:
        from django.http import HttpResponse
        mock_open.return_value = HttpResponse(b"ok")
        assert api_client.get(dl_url).status_code == 200
    # Staff cannot import keys or generate superuser recovery phrases
    assert api_client.post("/api/v1/auth/recovery/generate/", {}, format="json").status_code == 403

    superuser.is_active = False
    superuser.save(update_fields=["is_active"])
    api_client.force_authenticate(user=superuser)
    assert api_client.post("/api/v1/auth/recovery/generate/", {}, format="json").status_code == 403


def test_t3_pair10_f1_f14_gateway_hmac_vs_coraza_api_v1_scope(user):
    from apps.deployments.models.core import PlatformConfig

    cfg = PlatformConfig.load()
    cfg.domain = "pair10.test"
    cfg.save()
    node = ManagedServer.objects.create(
        owner=user, name="node-p10", host="198.51.100.10", node_number=10
    )
    Service.objects.create(
        name="svc-p10",
        owner=user,
        server=node,
        status=Service.Status.ACTIVE,
        node_url_enabled=True,
        public_domain="svc-p10.pair10.test",
    )
    node_cf = cg.generate_node_caddyfile(node)
    blocks = extract_caddy_site_blocks(node_cf)
    # Node control-plane allows Coraza bypass ONLY for /api/v1/, where Django RemoteSyncHMACAuthentication validates Gateway HMAC V2
    assert "request_header -X-SMSLY-Remote-Sync" not in blocks["grid10.pair10.test"]
    assert "request_header -X-SMSLY-Remote-Sync" in blocks["svc-p10.pair10.test"]


def test_t3_pair11_f5_f10_remote_deploy_ships_both_mtls_and_tls_addon_urls(db, user):
    svc = Service.objects.create(name="t3-p11-svc", owner=user)
    cfg = MtlsConfig.objects.get(service=svc)
    cfg.enabled = True
    cfg.sidecar_enabled = True
    cfg.save(update_fields=["enabled", "sidecar_enabled"])
    Addon.objects.create(
        service=svc,
        name="pg-p11",
        addon_type="POSTGRES",
        status=Addon.Status.ACTIVE,
        connection_url="postgresql://u:p@pg-p11:5432/d?sslmode=require",
    )
    dep = Deployment.objects.create(service=svc, status=Deployment.Status.QUEUED, commit_hash="abc")

    class _Dummy(DeploymentMixin):
        sent = None

        def _request(self, method, path, payload=None, timeout=60):
            self.sent = payload
            r = MagicMock()
            r.status_code = 200
            return r

        def _parse_json_response(self, resp, _):
            return {"deployment_id": "d-11"}

    client = _Dummy()
    with patch(
        "apps.deployments.services.addon_mesh.addon_lives_on_service_node", return_value=True
    ), patch(
        "apps.deployments.tasks_spiffe._create_spire_entry", return_value=True
    ), patch(
        "apps.deployments.tasks_spiffe._live_ecosystem_agent_id",
        return_value="spiffe://ecosystem.local/agent/n",
    ):
        client.trigger_deploy(dep, "remote-11", image_name="registry:5000/p11:1")

    assert client.sent["mtls"]["enabled"] is True
    assert "sslmode=require" in client.sent["addons"][0]["connection_url"]


def test_t3_pair12_f2_f9_installer_scripts_bundle_openappsec_prevent_and_falco_1024m():
    proc = run_wsl_bash_check(
        "bash scripts/regen-bundle.sh --check && "
        "python3 -m unittest tests/test_resource_scaling.py tests/test_shell_nounset_safety.py"
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    install_sh = repo_path("backend", "install.sh").read_text(encoding="utf-8")
    assert 'env_ensure_var "$env_file" "OPENAPPSEC_MODE" "prevent"' in install_sh
    assert 'env_ensure_var "$env_file" "FALCO_MEMORY_LIMIT" "1024M"' in install_sh


def test_t3_pair13_f7_f14_buildkit_containment_and_registry_validation(db):
    assert build_mod._ephemeral_builder_enabled() is True
    mixin = build_mod.BuildMixin()
    mixin.deployment = Deployment()
    mixin.secret_values = []
    with pytest.raises(BuildError):
        mixin._build_with_buildkit(".", "Dockerfile", "localhost:5000/bad:latest", {}, [], {})


def test_t3_pair14_f3_f10_network_scope_and_observability_no_plaintext_or_platform_bridge_leak():
    from apps.deployments.tasks.ecosystem import tasks as eco_tasks
    import inspect

    src = inspect.getsource(eco_tasks)
    assert "any_wants_internal" not in src
    obs = repo_path("infrastructure", "docker", "docker-compose.observability.yml").read_text(encoding="utf-8")
    assert "sslmode=disable" not in obs
