"""E2E security tests for R4 Control-Plane Fixes (recovery, server backups, route recheck, election, token scope, webhooks, lifecycle)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.cloud.models.backup import ServiceBackup
from apps.cloud.services import backup_service
from apps.cloud.webhooks import secrets as wh_secrets
from apps.core.middleware.security import SecurityMiddleware
from apps.core.models.api_token import APIToken
from apps.deployments.models import Deployment, Service
from apps.deployments.models.backup import ServerBackup
from apps.deployments.models.core import PlatformConfig
from apps.deployments.models.election import ClusterState
from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models.servers import ManagedServer
from apps.deployments.services.pipeline import build as build_mod
from apps.deployments.tasks.deploy.deletion import delete_service_task
from apps.deployments.tasks.infra.tasks_election import heartbeat_task
from apps.deployments.tasks.recover_stalled_deployments import recover_stalled_deployments
from apps.deployments.views.route import RouteRecheckView
from tests.e2e_security.helpers import run_wsl_bash_check, sign_election_v2, sign_gateway_v2

pytestmark = [pytest.mark.security]


# ============================================================================
# F12: R4 recovery.py (POST + is_active + TrustedIdentMixin atomic lockout)
# ============================================================================


def test_f12_recovery_generate_requires_post_and_active_superuser(api_client, user, superuser):
    api_client.force_authenticate(user=user)
    assert api_client.post("/api/v1/auth/recovery/generate/", {}, format="json").status_code == 403

    api_client.force_authenticate(user=superuser)
    assert api_client.get("/api/v1/auth/recovery/generate/").status_code == 405

    ok = api_client.post("/api/v1/auth/recovery/generate/", {}, format="json")
    assert ok.status_code == 200
    assert len(ok.data["phrase"].split()) == 12

    superuser.is_active = False
    superuser.save(update_fields=["is_active"])
    inactive_resp = api_client.post("/api/v1/auth/recovery/generate/", {}, format="json")
    assert inactive_resp.status_code == 403


def test_f12_recovery_restricted_token_cannot_overwrite_phrase(api_client, superuser):
    cfg = PlatformConfig.load()
    cfg.recovery_phrase_hash = "sentinel-hash"
    cfg.save(update_fields=["recovery_phrase_hash"])

    _tok, raw = APIToken.create_token(superuser, scopes=["read", "write"])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    resp = api_client.post("/api/v1/auth/recovery/generate/", {}, format="json")
    assert resp.status_code == 403
    cfg.refresh_from_db()
    assert cfg.recovery_phrase_hash == "sentinel-hash"


def test_f12_recovery_verify_lockout_ignores_spoofed_xff(api_client, superuser):
    from apps.core.views import recovery as rec_views

    cfg = PlatformConfig.load()
    cfg.recovery_phrase_hash = json.dumps({"hash": "bad", "salt": "bad"})
    cfg.save(update_fields=["recovery_phrase_hash"])

    payload = {
        "phrase": "one two three four five six seven eight nine ten eleven twelve",
        "username": superuser.username,
    }
    with patch.object(rec_views.RecoveryPhraseThrottle, "allow_request", return_value=True), patch.object(
        rec_views, "verify_recovery_phrase", return_value=False
    ):
        for i in range(10):
            r = api_client.post(
                "/api/v1/auth/recovery/verify/",
                payload,
                format="json",
                REMOTE_ADDR="198.51.100.88",
                HTTP_X_FORWARDED_FOR=f"203.0.113.{i + 1}",
            )
            assert r.status_code == 403
        locked = api_client.post(
            "/api/v1/auth/recovery/verify/",
            payload,
            format="json",
            REMOTE_ADDR="198.51.100.88",
            HTTP_X_FORWARDED_FOR="203.0.113.250",
        )
    assert locked.status_code == 429


# ============================================================================
# F13: R4 server_backup.py (IsAdminUser, signed non-bypass, non-UUID -> 404)
# ============================================================================


def test_f13_server_backup_download_and_key_require_admin_even_with_signed(
    api_client, user, staff_user, superuser
):
    backup = ServerBackup.objects.create(
        status="COMPLETED", file_path="/tmp/fake-bkp.tar.gz", size_bytes=16
    )
    dl_url = reverse("server-backup-download", args=[backup.id])
    dk_url = reverse("server-backup-download-key", args=[backup.id])

    # Anonymous even with ?signed=
    assert api_client.get(dl_url + "?signed=valid").status_code in (401, 403)

    # Regular authenticated user blocked even with ?signed=
    api_client.force_authenticate(user=user)
    assert api_client.get(dl_url + "?signed=valid").status_code == 403
    assert api_client.get(dk_url).status_code == 403

    # Non-UUID pk returns 404 (not 500 ValidationError)
    api_client.force_authenticate(user=superuser)
    assert api_client.get(reverse("server-backup-download", args=["not-a-uuid"])).status_code == 404

    # Empty file_path with cloud_uploaded=True returns 404 (not TypeError)
    cloud_bkp = ServerBackup.objects.create(
        status="COMPLETED", file_path="", cloud_uploaded=True, size_bytes=4
    )
    assert api_client.get(reverse("server-backup-download", args=[cloud_bkp.id])).status_code == 404


# ============================================================================
# F14: R4 route.py, election.py, token scopes, webhooks, lifecycle, svid_fetch
# ============================================================================


def test_f14_route_recheck_is_read_only_and_throttles_spoofed_xff(db, user):
    svc = Service.objects.create(name="f14-route-svc", owner=user, health_status="unhealthy")
    client = APIClient()
    view = RouteRecheckView.as_view()
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    with patch("apps.deployments.views.route._service_for_domain", return_value=svc), patch(
        "apps.core.services.health_monitor.reset_restart_state"
    ) as mock_reset, patch(
        "apps.core.services.health_monitor._check_service_health"
    ) as mock_check:
        req1 = factory.post(
            "/api/v1/system/route-recheck/",
            {"host": "known.example.test"},
            format="json",
            REMOTE_ADDR="198.51.100.77",
            HTTP_X_FORWARDED_FOR="203.0.113.10",
        )
        req2 = factory.get(
            "/api/v1/system/route-recheck/?host=known.example.test",
            REMOTE_ADDR="198.51.100.77",
            HTTP_X_FORWARDED_FOR="203.0.113.11",
        )
        r1 = view(req1)
        r2 = view(req2)

    assert r1.status_code == 200
    assert r1.data["health_status"] == "unhealthy"
    assert r2.status_code == 429
    mock_reset.assert_not_called()
    mock_check.assert_not_called()
    _ = client


def test_f14_election_hmac_replay_and_gateway_exemption_scope(db, user):
    from rest_framework.test import APIRequestFactory
    from apps.core.views.election import heartbeat_receive

    mesh = MeshNetwork.objects.create(name="e2e-election-mesh")
    local = ManagedServer.objects.create(
        owner=user, name="elec-local", host="192.0.2.1", gateway_secret="sec-local"
    )
    remote = ManagedServer.objects.create(
        owner=user, name="elec-remote", host="192.0.2.2", gateway_secret="sec-remote"
    )
    WireGuardPeer.objects.create(
        mesh=mesh, server=local, wg_address="10.100.0.1", is_local=True, public_key="k1", private_key="p1"
    )
    WireGuardPeer.objects.create(
        mesh=mesh, server=remote, wg_address="10.100.0.2", is_local=False, public_key="k2", private_key="p2"
    )
    cluster = ClusterState.objects.create(mesh=mesh, term=1)

    payload = {
        "term": 2,
        "sender_wg_address": "10.100.0.2",
        "leader_wg_address": "10.100.0.2",
    }
    body, hdrs = sign_election_v2("10.100.0.2", payload, "sec-remote")
    wsgi_hdrs = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in hdrs.items()}
    factory = APIRequestFactory()

    with patch("builtins.open"), patch("apps.deployments.services.heartbeat_bus.publish_heartbeat"):
        req1 = factory.post("/api/v1/internal/heartbeat/", body, content_type="application/json", **wsgi_hdrs)
        resp1 = SecurityMiddleware(heartbeat_receive)(req1)
        assert resp1.status_code == 200

        # Exact nonce replay must be rejected
        req2 = factory.post("/api/v1/internal/heartbeat/", body, content_type="application/json", **wsgi_hdrs)
        resp2 = SecurityMiddleware(heartbeat_receive)(req2)
        assert resp2.status_code in (401, 403)

    cluster.refresh_from_db()
    assert cluster.term == 2

    # Unrelated internal route must NOT be exempted from gateway HMAC
    extra_req = factory.post("/api/v1/internal/heartbeat/extra/", b"{}", content_type="application/json")
    extra_resp = SecurityMiddleware(lambda r: None)(extra_req)
    assert extra_resp.status_code == 403


def test_f14_tasks_election_heartbeat_dispatches_per_mesh_without_unused_helper(db):
    import apps.deployments.tasks.infra.tasks_election as te
    from apps.deployments.services.election_service import ElectionService

    assert not hasattr(te, "_get_local_role")
    mesh = MeshNetwork.objects.create(name="e2e-hb-mesh")
    ClusterState.objects.create(mesh=mesh, local_role="LEADER")
    seen = []
    with patch.object(
        ElectionService, "send_heartbeat", side_effect=lambda c: seen.append(c.local_role)
    ), patch.object(ElectionService, "check_leader_timeout", return_value=False):
        te.heartbeat_task()
    assert seen == ["LEADER"]


def test_f14_webhook_secret_fails_closed_on_platform_config_error():
    with patch(
        "apps.deployments.models.core.PlatformConfig.load",
        side_effect=RuntimeError("db down"),
    ):
        assert wh_secrets.resolve_webhook_secret("github") == ""


def test_f14_loopback_registry_refused_by_contained_buildkit():
    mixin = build_mod.BuildMixin()
    mixin.deployment = Deployment()
    mixin.secret_values = []
    with pytest.raises(Exception, match="registry:5000"):
        mixin._build_with_buildkit(".", "Dockerfile", "127.0.0.1:5000/app:v1", {}, [], {})


def test_f14_svid_fetch_shell_safety_and_timeouts():
    proc = run_wsl_bash_check("python3 -m unittest tests/test_svid_fetch_safety.py")
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_f14_lifecycle_rollback_and_deletion_backup_purge(db, user):
    prov = CloudProvider.objects.create(name="f14-local", provider_type="LOCAL", is_active=True)
    svc = Service.objects.create(name="f14-life-svc", owner=user, provider=prov, branch="main")
    Deployment.objects.create(service=svc, status="ACTIVE", commit_hash="v1", finished_at=timezone.now())
    Deployment.objects.create(service=svc, status="ACTIVE", commit_hash="v2", finished_at=timezone.now())

    handle, path = tempfile.mkstemp(prefix="f14-bkp-")
    os.close(handle)
    bkp = ServiceBackup.objects.create(service=svc, status="COMPLETED", file_path=path)

    with patch(
        "apps.deployments.services.deletion_orchestrator.DeletionOrchestrator"
    ) as orch, patch(
        "apps.deployments.services.traefik_manager.canary_file.remove_canary_file"
    ), patch(
        "apps.deployments.utils.target.resolve_active_execution_target",
        return_value={"server_obj": None},
    ):
        orch.return_value.delete_service_resources.return_value = True
        delete_service_task.run(str(svc.pk))

    assert not Service.objects.filter(pk=svc.pk).exists()
    assert not ServiceBackup.objects.filter(pk=bkp.pk).exists()
    assert not os.path.exists(path)


def test_f12_recovery_generate_and_verify_end_to_end_happy_path(api_client, superuser):
    api_client.force_authenticate(user=superuser)
    gen = api_client.post("/api/v1/auth/recovery/generate/", {}, format="json")
    assert gen.status_code == 200
    phrase = gen.data["phrase"]

    anon = APIClient()
    verify = anon.post(
        "/api/v1/auth/recovery/verify/",
        {"phrase": phrase, "username": superuser.username},
        format="json",
        REMOTE_ADDR="198.51.100.123",
    )
    assert verify.status_code == 200
    assert verify.data.get("key")


def test_f12_recovery_verify_wrong_phrase_and_non_superuser_rejected(api_client, user, superuser):
    api_client.force_authenticate(user=superuser)
    gen = api_client.post("/api/v1/auth/recovery/generate/", {}, format="json")
    assert gen.status_code == 200
    valid_phrase = gen.data["phrase"]

    anon = APIClient()
    bad_phrase = anon.post(
        "/api/v1/auth/recovery/verify/",
        {"phrase": "wrong words " * 6, "username": superuser.username},
        format="json",
        REMOTE_ADDR="198.51.100.124",
    )
    assert bad_phrase.status_code == 403

    # Wrong phrase for regular user also fails at phrase check (403);
    # even with a valid phrase, a non-superuser account is rejected (404).
    non_super_bad = anon.post(
        "/api/v1/auth/recovery/verify/",
        {"phrase": "any twelve words here " * 3, "username": user.username},
        format="json",
        REMOTE_ADDR="198.51.100.125",
    )
    assert non_super_bad.status_code == 403

    non_super = anon.post(
        "/api/v1/auth/recovery/verify/",
        {"phrase": valid_phrase, "username": user.username},
        format="json",
        REMOTE_ADDR="198.51.100.126",
    )
    assert non_super.status_code == 404


def test_f12_recovery_cannot_be_invoked_with_unscoped_read_only_api_token_even_for_superuser(
    api_client, superuser
):
    _tok, raw = APIToken.create_token(superuser, scopes=["read"])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert api_client.post("/api/v1/auth/recovery/generate/", {}, format="json").status_code == 403


def test_f13_server_backup_staff_can_download_and_download_key_but_cannot_import_key(
    api_client, staff_user
):
    backup = ServerBackup.objects.create(
        status="COMPLETED", file_path="/tmp/staff-bkp.tar.gz", size_bytes=8
    )
    from django.http import HttpResponse

    api_client.force_authenticate(user=staff_user)
    with patch("apps.deployments.views.server_backup.os.path.exists", return_value=True), patch(
        "apps.deployments.views.server_backup._open_backup_download_response",
        return_value=HttpResponse(b"ok"),
    ):
        assert api_client.get(reverse("server-backup-download", args=[backup.id])).status_code == 200

    with patch("apps.deployments.views.server_backup.os.path.exists", return_value=True), patch(
        "apps.deployments.services.backup_service.BackupService.read_v2_header",
        return_value={"format": "CHUNKED_V2", "key_id": "k-staff", "fingerprint": "fp"},
    ):
        assert api_client.get(reverse("server-backup-download-key", args=[backup.id])).status_code == 200

    assert api_client.post(
        reverse("server-backup-import-key"),
        {"key_id": "k1", "key_material": "m"},
        format="json",
    ).status_code == 403


def test_f13_server_backup_list_and_create_blocked_for_regular_user(api_client, user):
    api_client.force_authenticate(user=user)
    assert api_client.get("/api/v1/server/backups/").status_code == 403
    assert api_client.post("/api/v1/server/backups/", {}, format="json").status_code == 403


def test_f14_api_token_project_scope_enforced_across_rest_and_mcp(api_client, user):
    _tok, raw = APIToken.create_token(user, scopes=["read"], projects=[str(uuid.uuid4())])
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert api_client.get("/api/v1/services/").status_code == 403
    assert api_client.get("/api/v1/mcp/tools/").status_code == 200
    assert api_client.post("/api/v1/mcp/tools/list_services/call/", {"args": {}}, format="json").status_code == 403


def test_f14_route_recheck_unknown_domain_returns_404_without_mutating(db):
    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    view = RouteRecheckView.as_view()
    with patch("apps.deployments.views.route._service_for_domain", return_value=None), patch(
        "apps.core.services.health_monitor.reset_restart_state"
    ) as mock_reset:
        resp = view(factory.get("/api/v1/system/route-recheck/?host=unknown.example.com", REMOTE_ADDR="198.51.100.99"))
    assert resp.status_code == 404
    mock_reset.assert_not_called()


def test_f12_recovery_single_use_consumption_expiry_and_revoke(api_client, superuser):
    api_client.force_authenticate(user=superuser)
    gen = api_client.post("/api/v1/auth/recovery/generate/", {}, format="json")
    assert gen.status_code == 200
    assert gen.data["single_use"] is True
    assert "created_at" in gen.data and "expires_at" in gen.data
    phrase = gen.data["phrase"]

    anon = APIClient()
    first = anon.post(
        "/api/v1/auth/recovery/verify/",
        {"phrase": phrase, "username": superuser.username},
        format="json",
        REMOTE_ADDR="198.51.100.201",
    )
    assert first.status_code == 200
    assert first.data["phrase_consumed"] is True

    # Replay of the consumed recovery phrase must fail closed
    replay = anon.post(
        "/api/v1/auth/recovery/verify/",
        {"phrase": phrase, "username": superuser.username},
        format="json",
        REMOTE_ADDR="198.51.100.202",
    )
    assert replay.status_code == 400

    # Explicit revoke endpoint clears stored hash
    gen2 = api_client.post("/api/v1/auth/recovery/generate/", {}, format="json")
    assert gen2.status_code == 200
    rev = api_client.post("/api/v1/auth/recovery/revoke/", {}, format="json")
    assert rev.status_code == 200
    assert rev.data["revoked"] is True
    assert rev.data["had_phrase"] is True
    PlatformConfig.clear_cache()
    assert not (PlatformConfig.load().recovery_phrase_hash or "").strip()


def test_f14_untrusted_public_peer_cannot_spoof_cf_connecting_ip():
    from apps.core.rate_limiting import TrustedIdentMixin, get_trusted_client_ip
    from types import SimpleNamespace

    fake_req = SimpleNamespace(
        META={
            "REMOTE_ADDR": "198.51.100.55",
            "HTTP_CF_CONNECTING_IP": "203.0.113.99",
            "HTTP_X_FORWARDED_FOR": "203.0.113.99",
            "HTTP_X_REAL_IP": "203.0.113.99",
        },
        user=None,
    )
    assert TrustedIdentMixin().get_ident(fake_req) == "198.51.100.55"
    assert get_trusted_client_ip(fake_req) == "198.51.100.55"


