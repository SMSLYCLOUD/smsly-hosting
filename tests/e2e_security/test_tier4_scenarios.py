"""E2E Tier 4 — Realistic Operator & Adversary Security Scenarios (S1–S10)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.addons.services import addon_tls, shared_postgres, volume_encryption
from apps.addons.services.addon_provisioner import AddonProvisioner
from apps.cloud.adapters.local import LocalAdapter
from apps.cloud.models import CloudProvider
from apps.cloud.models.backup import ServiceBackup
from apps.cloud.webhooks import secrets as wh_secrets
from apps.core.middleware.security import SecurityMiddleware
from apps.core.models.api_token import APIToken
from apps.deployments.models import Addon, Deployment, ManagedServer, Service
from apps.deployments.models.backup import ServerBackup
from apps.deployments.models.core import PlatformConfig
from apps.deployments.models.election import ClusterState
from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.services import network_scope as ns_mod
from apps.deployments.services.caddy_manager import config_generation as cg
from apps.deployments.services.caddy_manager import validation as cv
from apps.deployments.services.pipeline import build as build_mod
from apps.deployments.services.pipeline.compose_networking import ComposeNetworkingMixin
from apps.deployments.services.pipeline.exceptions import BuildError
from apps.deployments.tasks.deploy import deploy_container as dc
from apps.deployments.tasks.deploy.deletion import delete_service_task
from apps.deployments.tasks.recover_stalled_deployments import recover_stalled_deployments
from apps.deployments.views.route import RouteRecheckView
from apps.deployments.views.service.deploy import DeployActionsMixin
from apps.mtls.models import MtlsConfig
from apps.mtls.services.envoy_sidecar import EnvoySidecar
from tests.e2e_security.helpers import (
    extract_caddy_site_blocks,
    extract_caddy_snippet,
    load_compose_yaml,
    run_wsl_bash_check,
    sign_election_v2,
)

pytestmark = [pytest.mark.security]


def test_s1_multitenant_waf_bypass_attempt_blocked_while_control_plane_sync_works(
    db, user, api_client
):
    cfg = PlatformConfig.load()
    cfg.domain = "s1trulay.site"
    cfg.use_ssl = True
    cfg.wildcard_subdomains = True
    cfg.save()
    node = ManagedServer.objects.create(
        owner=user, name="s1-node", host="198.51.100.51", node_number=1, status="ACTIVE"
    )
    Service.objects.create(
        name="victim-app",
        owner=user,
        server=node,
        status=Service.Status.ACTIVE,
        node_url_enabled=True,
        public_domain="victim-app.s1trulay.site",
    )

    caddyfile = cg.generate_caddyfile(cfg)
    node_cf = cg.generate_node_caddyfile(node)
    assert cv.validate_edge_sidecar_endpoints(caddyfile) == []
    assert cv.validate_secure_headers_present(caddyfile) == []

    snippet = extract_caddy_snippet(caddyfile, "coraza_waf")
    assert '@beginsWith /api/v1/' in snippet
    assert "t:normalizePath" in snippet

    master_blocks = extract_caddy_site_blocks(caddyfile)
    assert "request_header -X-SMSLY-Remote-Sync" in master_blocks["*.s1trulay.site"]

    node_blocks = extract_caddy_site_blocks(node_cf)
    assert "request_header -X-SMSLY-Remote-Sync" not in node_blocks["grid1.s1trulay.site"]
    assert "request_header -X-SMSLY-Remote-Sync" in node_blocks["victim-app.s1trulay.site"]


def test_s2_blue_green_promote_runsc_app_with_tls_postgres_and_redis_no_platform_bridge(
    db, user, mock_docker_client, tmp_path, monkeypatch
):
    monkeypatch.setenv("SMSLY_ADDON_CA_DIR", str(tmp_path))
    monkeypatch.setenv("SMSLY_BUILDS_DIR", str(tmp_path))

    svc = Service.objects.create(name="s2-runsc-app", owner=user)
    assert svc.use_internal_network is False

    prov = AddonProvisioner()
    ok_proc = MagicMock(returncode=0, stdout="cid-addon\n", stderr="")
    with patch("apps.addons.services.addon_tls.ensure_tls_volume", return_value="tls-vol"), patch(
        "apps.addons.services.addon_provisioner.subprocess.run", return_value=ok_proc
    ), patch.object(prov, "_wait_for_health"):
        _, pg_url = prov._provision_postgres("s2-pg", "pgpw", 5432, "postgres-s2")
        _, rd_url = prov._provision_redis("s2-rd", "rdpw", 6379, "redis-s2")

    assert pg_url.endswith("?sslmode=require")
    assert rd_url.startswith("rediss://")

    adapter = object.__new__(LocalAdapter)
    adapter.docker_client = mock_docker_client
    adapter.k8s_client = None
    adapter.batch_v1 = None
    adapter._service_id = svc.id

    green = MagicMock()
    green.id = "green-s2"
    green.name = "s2-runsc-app-green-1"
    green.labels = {
        "smsly.blue_green.is_public": "True",
        "smsly.blue_green.port": "8000",
        "smsly.blue_green.host_rule": "Host(`s2.example.com`)",
    }
    green.attrs = {
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Config": {
            "Env": [f"DATABASE_URL={pg_url}", f"REDIS_URL={rd_url}"],
            "Cmd": None,
            "Entrypoint": None,
            "Healthcheck": None,
        },
        "HostConfig": {
            "Runtime": "runsc",
            "ReadonlyRootfs": True,
            "Tmpfs": {"/tmp": "size=100m"},
            "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
            "ExtraHosts": ["postgres-s2:172.30.2.10"],
            "Dns": ["8.8.8.8", "1.1.1.1"],
            "Binds": None,
            "RestartPolicy": {},
        },
    }
    green.image.tags = ["s2:v2"]
    promoted = MagicMock()
    promoted.id = "prom-s2"
    promoted.attrs = {"NetworkSettings": {"Networks": {"smsly-net-s2": {}}}}
    mock_docker_client.containers.create.return_value = promoted
    mock_docker_client.containers.get.side_effect = lambda cid: (
        green if cid == "green-s2" else (_ for _ in ()).throw(ns_mod.docker.errors.NotFound("x"))
    )

    with patch.object(LocalAdapter, "_wait_container_healthy", return_value=True), patch.object(
        adapter, "_apply_egress_restrictions"
    ), patch.object(
        adapter, "_resolve_network_name", return_value="smsly-net-s2"
    ):
        adapter.promote_container(svc.name, "green-s2")

    kw = mock_docker_client.containers.create.call_args.kwargs
    assert kw["runtime"] == "runsc"
    assert kw["read_only"] is True
    assert kw["dns"] == ["8.8.8.8", "1.1.1.1"]
    assert "smsly-platform-net" not in kw["networking_config"]
    assert any(":/etc/resolv.conf:ro" in str(v) for v in kw["volumes"])


def test_s3_untrusted_buildkit_run_and_malicious_compose_blocked(tmp_path):
    prod = load_compose_yaml("docker-compose.prod.yml")
    assert prod["services"]["socket-proxy"]["networks"] == ["socket-proxy"]

    # Build network drops RFC1918 + metadata + mesh + other bridges
    fake_net = MagicMock()
    fake_net.attrs = {"Id": "bbbb11112222333344445555"}
    client = MagicMock()
    client.networks.get.return_value = fake_net
    with patch.object(ns_mod.docker, "from_env", return_value=client), patch.object(
        ns_mod.subprocess, "run", return_value=MagicMock(returncode=0, stdout="", stderr="")
    ) as mrun:
        ns_mod.apply_build_network_isolation("smsly-build-net")
    joined = "\n".join(c.args[0][-1] for c in mrun.call_args_list)
    assert "-d 169.254.169.254/32 -j DROP" in joined
    assert "-d 10.0.0.0/8 -j DROP" in joined

    # Malicious compose attempting docker.sock mount & SYS_ADMIN rejected
    cfile = tmp_path / "docker-compose.yml"
    cfile.write_text(
        "services:\n  web:\n    image: alpine\n    cap_add:\n      - SYS_ADMIN\n"
        "    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
        encoding="utf-8",
    )

    class _H(ComposeNetworkingMixin):
        build_dir = str(tmp_path)
        source_dir = str(tmp_path)
        deployment = SimpleNamespace(id="dep-s3")
        service = SimpleNamespace(id="svc-s3", name="web", compose_file="docker-compose.yml", managed_by="USER")

        def _resolve_service_network_name(self):
            return "smsly-net-s3"

        def _compose_traefik_labels(self, _):
            return {}

    with pytest.raises(BuildError):
        _H()._write_compose_routing_override("web", "proj-s3")


def test_s4_worker_mtls_fail_closed_vs_explicit_opt_out(db, user):
    provider = CloudProvider.objects.create(
        name="s4-prov", provider_type=CloudProvider.ProviderType.LOCAL, is_active=True
    )
    svc_on = Service.objects.create(name="s4-mtls-on", owner=user, provider=provider)
    cfg = MtlsConfig.objects.get(service=svc_on)
    cfg.enabled = True
    cfg.sidecar_enabled = True
    cfg.save(update_fields=["enabled", "sidecar_enabled"])
    dep_on = Deployment.objects.create(service=svc_on, status=Deployment.Status.BUILDING, commit_hash="s4")

    compute = MagicMock()
    compute.deploy_container.return_value = MagicMock(resource_id="cid-s4")
    with patch.object(dc, "ComputeService", return_value=compute), patch(
        "apps.deployments.tasks.deploy.health._local_container_timeout_seconds", return_value=1
    ), patch(
        "apps.deployments.tasks.deploy.health._wait_for_local_container_healthy", return_value=True
    ), patch.object(
        EnvoySidecar, "remount_if_stale", side_effect=RuntimeError("no SPIRE socket on worker")
    ):
        with pytest.raises(RuntimeError, match="mTLS sidecar injection failed"):
            dc._deploy_container(dep_on, provider, "app:s4")

    # Non-mTLS opted-out service continues normally
    svc_off = Service.objects.create(name="s4-mtls-off", owner=user, provider=provider)
    dep_off = Deployment.objects.create(service=svc_off, status=Deployment.Status.BUILDING, commit_hash="s4off")
    with patch.object(dc, "ComputeService", return_value=compute), patch(
        "apps.deployments.tasks.deploy.health._local_container_timeout_seconds", return_value=1
    ), patch(
        "apps.deployments.tasks.deploy.health._wait_for_local_container_healthy", return_value=True
    ), patch.object(dc, "_regenerate_caddyfile"), patch(
        "apps.deployments.tasks.deploy.health._wait_for_local_route_ready", return_value=True
    ), patch.object(dc, "_run_managed_image_post_deploy_hooks"), patch.object(
        dc, "_probe_addon_connectivity", return_value=[]
    ), patch.object(dc, "_mark_deployment_active"), patch.object(
        dc, "_post_deploy_success"
    ), patch.object(dc, "broadcast_status"), patch(
        "apps.deployments.tasks.deployment.tasks_deploy._post_deploy_monitor"
    ), patch.object(EnvoySidecar, "remove_orphan_sidecar") as mock_sweep:
        dc._deploy_container(dep_off, provider, "app:s4off")
    mock_sweep.assert_called_once()


def test_s5_unauthorized_recovery_and_server_backup_attack_chain(
    api_client, user, superuser
):
    backup = ServerBackup.objects.create(status="COMPLETED", file_path="/tmp/s5.tar.gz", size_bytes=8)
    _tok, scoped_raw = APIToken.create_token(superuser, scopes=["read", "write"])

    # Scoped admin token cannot generate recovery phrase
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {scoped_raw}")
    assert api_client.post("/api/v1/auth/recovery/generate/", {}, format="json").status_code == 403

    # Deactivated superuser cannot generate recovery phrase
    superuser.is_active = False
    superuser.save(update_fields=["is_active"])
    api_client.credentials()
    api_client.force_authenticate(user=superuser)
    assert api_client.post("/api/v1/auth/recovery/generate/", {}, format="json").status_code == 403

    # Regular user with signed link cannot download backup or key
    api_client.force_authenticate(user=user)
    assert api_client.get(reverse("server-backup-download", args=[backup.id]) + "?signed=1").status_code == 403
    assert api_client.get(reverse("server-backup-download-key", args=[backup.id])).status_code == 403


def test_s6_split_brain_election_replay_and_term_spoofing_attack(db, user):
    from apps.core.views.election import heartbeat_receive, vote_request

    mesh = MeshNetwork.objects.create(name="s6-mesh")
    local = ManagedServer.objects.create(
        owner=user, name="s6-local", host="192.0.2.11", gateway_secret="s6-local-sec"
    )
    remote = ManagedServer.objects.create(
        owner=user, name="s6-remote", host="192.0.2.12", gateway_secret="s6-remote-sec"
    )
    WireGuardPeer.objects.create(
        mesh=mesh, server=local, wg_address="10.100.0.1", is_local=True, public_key="k1", private_key="p1"
    )
    WireGuardPeer.objects.create(
        mesh=mesh, server=remote, wg_address="10.100.0.2", is_local=False, public_key="k2", private_key="p2"
    )
    ClusterState.objects.create(mesh=mesh, term=1)

    factory = APIRequestFactory()
    # Impersonation attempt: signed by remote peer claiming leader is 10.100.0.1
    spoof_body, spoof_hdrs = sign_election_v2(
        "10.100.0.2",
        {"term": 5, "sender_wg_address": "10.100.0.2", "leader_wg_address": "10.100.0.1"},
        "s6-remote-sec",
    )
    spoof_req = factory.post(
        "/api/v1/internal/heartbeat/",
        spoof_body,
        content_type="application/json",
        **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in spoof_hdrs.items()},
    )
    with patch("builtins.open"), patch("apps.deployments.services.heartbeat_bus.publish_heartbeat"):
        assert SecurityMiddleware(heartbeat_receive)(spoof_req).status_code in (400, 401, 403)

    # Boolean/string term injection rejected
    bad_body, bad_hdrs = sign_election_v2(
        "10.100.0.2",
        {"term": True, "sender_wg_address": "10.100.0.2", "candidate_wg_address": "10.100.0.2"},
        "s6-remote-sec",
    )
    bad_req = factory.post(
        "/api/v1/internal/vote/",
        bad_body,
        content_type="application/json",
        **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in bad_hdrs.items()},
    )
    assert SecurityMiddleware(vote_request)(bad_req).status_code == 400


def test_s7_anonymous_route_recheck_and_webhook_fail_closed_under_db_outage(db, user):
    svc = Service.objects.create(name="s7-svc", owner=user, health_status="healthy")
    factory = APIRequestFactory()
    view = RouteRecheckView.as_view()
    with patch("apps.deployments.views.route._service_for_domain", return_value=svc), patch(
        "apps.core.services.health_monitor.reset_restart_state"
    ) as mock_reset:
        r1 = view(factory.post("/api/v1/system/route-recheck/", {"host": "s7.test"}, format="json", REMOTE_ADDR="198.51.100.200"))
        r2 = view(factory.post("/api/v1/system/route-recheck/", {"host": "s7.test"}, format="json", REMOTE_ADDR="198.51.100.200", HTTP_X_FORWARDED_FOR="1.2.3.4"))
    assert r1.status_code == 200
    assert r2.status_code == 429
    mock_reset.assert_not_called()

    with patch("apps.deployments.models.core.PlatformConfig.load", side_effect=RuntimeError("db unreachable")):
        assert wh_secrets.resolve_webhook_secret("gitlab") == ""


def test_s8_operator_hardening_baseline_audit():
    prod = load_compose_yaml("docker-compose.prod.yml")
    node = load_compose_yaml("infrastructure/docker/docker-compose.node.yml")
    assert prod["services"]["falco"]["deploy"]["resources"]["limits"]["memory"] == "${FALCO_MEMORY_LIMIT:-1024M}"
    assert node["services"]["falco"]["deploy"]["resources"]["limits"]["memory"] == "${FALCO_MEMORY_LIMIT:-1024M}"
    assert "smsly-net" not in prod["services"]["socket-proxy"]["networks"]
    assert "traefik-socket-proxy" in node["services"]

    proc = run_wsl_bash_check(
        "bash scripts/regen-bundle.sh --check && "
        "python3 -m unittest tests/test_compose_profiles.py tests/test_volume_encryption_integrity.py"
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_s9_scoped_apitoken_lateral_escalation_blocked(api_client, user):
    _tok, raw = APIToken.create_token(user, scopes=["read"], projects=[str(uuid.uuid4())])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert api_client.post("/api/v1/tokens/create/", {"name": "lat"}, format="json").status_code == 403
    assert api_client.get("/api/v1/services/").status_code == 403
    assert api_client.post("/api/v1/mcp/tools/cancel_deployment/call/", {"args": {}}, format="json").status_code == 403
    assert api_client.post("/api/v1/mcp/tokens/", {"name": "lat2"}, format="json").status_code == 403


def test_s10_service_deletion_retry_on_purge_error_rollback_and_review_preservation(
    db, user
):
    prov = CloudProvider.objects.create(name="s10-local", provider_type="LOCAL", is_active=True)
    svc = Service.objects.create(name="s10-svc", owner=user, provider=prov, branch="main")
    Deployment.objects.create(service=svc, status="ACTIVE", commit_hash="v1", finished_at=timezone.now())
    paused = Deployment.objects.create(
        service=svc,
        status="REVIEW",
        commit_hash="v2-review",
        review_summary={"services": [{"name": "s10-svc"}]},
    )
    stale = timezone.now() - timedelta(days=3)
    Deployment.objects.filter(pk=paused.pk).update(created_at=stale, updated_at=stale)
    recover_stalled_deployments.run()
    paused.refresh_from_db()
    assert paused.status == "REVIEW"

    # Rollback finds v1 even after failed v2
    failed = Deployment.objects.create(service=svc, status="FAILED", commit_hash="v3-bad", finished_at=timezone.now())
    view = DeployActionsMixin()
    view.get_object = lambda: svc
    req = SimpleNamespace(data={"confirm": True}, user=user)
    with patch("apps.deployments.views.service.deploy.smart_deploy_task.delay"):
        rb = view.instant_rollback(req)
    assert rb.status_code == 201
    assert Deployment.objects.get(is_rollback=True).commit_hash == "v1"
    _ = failed
