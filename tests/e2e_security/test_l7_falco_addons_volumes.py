"""E2E security tests for L7 (Worker Falco 1024M cap, Addon Transport TLS & Observability, Per-tenant Volume Encryption)."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding

from apps.addons.services import addon_tls, shared_postgres, tenant_pooler, volume_encryption
from apps.addons.services.addon_provisioner import AddonProvisioner
from apps.addons.services.db_proxy import DatabaseProxy
from apps.deployments.models import Addon, Service
from apps.deployments.models.storage import Volume
from tests.e2e_security.helpers import load_compose_yaml, repo_path, run_wsl_bash_check

pytestmark = [pytest.mark.security]


# ============================================================================
# F9: L7 Worker Falco OOM Fix (1024M default across compose, env & scripts)
# ============================================================================


def test_f9_falco_memory_limit_1024m_across_compose_files():
    node = load_compose_yaml("infrastructure/docker/docker-compose.node.yml")
    prod = load_compose_yaml("docker-compose.prod.yml")
    falco_only = load_compose_yaml("infrastructure/docker/docker-compose.falco.yml")

    assert node["services"]["falco"]["deploy"]["resources"]["limits"]["memory"] == "${FALCO_MEMORY_LIMIT:-1024M}"
    assert prod["services"]["falco"]["deploy"]["resources"]["limits"]["memory"] == "${FALCO_MEMORY_LIMIT:-1024M}"
    assert falco_only["services"]["falco"]["deploy"]["resources"]["limits"]["memory"] == "${FALCO_MEMORY_LIMIT:-1024M}"


def test_f9_falco_memory_limit_1024m_in_env_templates_and_bundle():
    env_ex = repo_path(".env.example").read_text(encoding="utf-8")
    fresh_cfg = repo_path("lib/fresh_config.sh").read_text(encoding="utf-8")
    plat_env = repo_path("lib/platform-env.sh").read_text(encoding="utf-8")
    install_sh = repo_path("backend/install.sh").read_text(encoding="utf-8")

    assert re.search(r"^FALCO_MEMORY_LIMIT=1024M\s*$", env_ex, re.MULTILINE)
    assert "FALCO_MEMORY_LIMIT=${FALCO_MEMORY_LIMIT:-1024M}" in fresh_cfg
    assert 'env_ensure_var "$env_file" "FALCO_MEMORY_LIMIT" "1024M"' in plat_env
    assert 'env_ensure_var "$env_file" "FALCO_MEMORY_LIMIT" "1024M"' in install_sh


def test_f9_bundle_in_sync_after_falco_update():
    proc = run_wsl_bash_check("bash scripts/regen-bundle.sh --check")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "bundle in sync" in proc.stdout


# ============================================================================
# F10: L7 Addon Transport TLS (Postgres, PgBouncer, Redis, Observability)
# ============================================================================


def test_f10_platform_addon_ca_and_signed_leaf_cert_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("SMSLY_ADDON_CA_DIR", str(tmp_path))
    ca_crt_pem, ca_key_pem = addon_tls.ensure_addon_ca()
    assert os.path.isfile(tmp_path / "ca.crt")
    assert os.path.isfile(tmp_path / "ca.key")
    assert ca_key_pem

    srv_ca_pem, srv_crt_pem, srv_key_pem = addon_tls.issue_addon_cert(
        "postgres-tenant",
        dns_names=["postgres-tenant", "smsly-addon-postgres-1", "localhost"],
        ip_addrs=["127.0.0.1", "10.100.0.2"],
    )
    assert srv_ca_pem == ca_crt_pem
    assert b"BEGIN PRIVATE KEY" in srv_key_pem

    ca_cert = x509.load_pem_x509_certificate(ca_crt_pem)
    srv_cert = x509.load_pem_x509_certificate(srv_crt_pem)
    ca_cert.public_key().verify(
        srv_cert.signature,
        srv_cert.tbs_certificate_bytes,
        padding.PKCS1v15(),
        srv_cert.signature_hash_algorithm,
    )


def test_f10_postgres_and_redis_provisioning_enforce_tls(db, user, tmp_path, monkeypatch):
    monkeypatch.setenv("SMSLY_ADDON_CA_DIR", str(tmp_path))
    prov = AddonProvisioner()
    ok_proc = MagicMock(returncode=0, stdout="deadbeef1234\n", stderr="")

    with patch(
        "apps.addons.services.addon_tls.ensure_tls_volume", return_value="pg-tls-vol"
    ), patch(
        "apps.addons.services.addon_provisioner.subprocess.run", return_value=ok_proc
    ) as mock_run, patch.object(prov, "_wait_for_health"):
        cid, pg_url = prov._provision_postgres("pg-ctr", "pw1", 5432, "pg-tenant")

    assert cid == "deadbeef1234"
    assert pg_url == "postgresql://pg_tenant:pw1@pg-tenant:5432/pg_tenant?sslmode=require"
    run_cmd = " ".join(mock_run.call_args_list[0].args[0])
    assert "ssl=on" in run_cmd
    assert "pg-ctr-tls:/etc/ssl/smsly-addon:ro" in run_cmd
    hba_cmd = " ".join(mock_run.call_args_list[-1].args[0])
    assert "hostssl" in hba_cmd or "pg_reload_conf" in " ".join(
        " ".join(c.args[0]) for c in mock_run.call_args_list[1:]
    )

    with patch(
        "apps.addons.services.addon_tls.ensure_tls_volume", return_value="rd-tls-vol"
    ), patch(
        "apps.addons.services.addon_provisioner.subprocess.run", return_value=ok_proc
    ) as mock_run_rd, patch.object(prov, "_wait_for_health"):
        _rcid, rd_url = prov._provision_redis("rd-ctr", "rpw", 6379, "rd-tenant")

    assert rd_url.startswith("rediss://")
    rd_cmd = " ".join(mock_run_rd.call_args_list[0].args[0])
    assert "--tls-port 6379 --port 0" in rd_cmd
    assert "--tls-cert-file /etc/redis/tls/server.crt" in rd_cmd
    assert "--tls-key-file /etc/redis/tls/server.key" in rd_cmd


def test_f10_pgbouncer_enforces_client_and_server_tls():
    ini, _userlist = tenant_pooler.render_tenants_config(
        [
            {
                "alias": "postgres-alpha",
                "user": "u_alpha",
                "db": "db_alpha",
                "password": "secret-password-alpha",
                "server": "smsly-shared-postgres",
            }
        ]
    )
    assert "client_tls_sslmode" in ini
    assert "server_tls_sslmode" in ini
    assert "disable" not in ini.lower()


def test_f10_db_proxy_upgrades_to_verify_ca_and_required_tls(tmp_path, monkeypatch):
    monkeypatch.setenv("SMSLY_ADDON_CA_DIR", str(tmp_path))
    ca_path = addon_tls.get_addon_ca_cert_path()

    pg_addon = SimpleNamespace(
        addon_type="POSTGRES",
        connection_url="postgresql://u:p@pg:5432/db?sslmode=require",
    )
    with patch("psycopg2.connect") as m_pg:
        DatabaseProxy(pg_addon)._build_pg_connection()
    assert m_pg.call_args.kwargs["sslmode"] == "verify-ca"
    assert m_pg.call_args.kwargs["sslrootcert"] == ca_path

    rd_addon = SimpleNamespace(
        addon_type="REDIS",
        connection_url="rediss://:pw@rd:6379/0?ssl_cert_reqs=none",
    )
    with patch("redis.from_url") as m_rd:
        DatabaseProxy(rd_addon)._build_redis_connection()
    assert m_rd.call_args.kwargs["ssl_cert_reqs"] == "required"
    assert m_rd.call_args.kwargs["ssl_ca_certs"] == ca_path


def test_f10_observability_compose_removes_sslmode_disable():
    obs = repo_path("infrastructure/docker/docker-compose.observability.yml").read_text(encoding="utf-8")
    assert "sslmode=disable" not in obs
    assert "@smsly-shared-postgres:5432/postgres?sslmode=require" in obs
    assert "@smsly-postgres-primary:5432/postgres?sslmode=prefer" in obs


def test_f10_ensure_pg_ssl_url_rewrites_sslmode_disable():
    assert addon_tls.ensure_pg_ssl_url("postgresql://u:p@h:5432/d?sslmode=disable") == "postgresql://u:p@h:5432/d?sslmode=require"
    assert addon_tls.ensure_redis_tls_url("redis://:p@h:6379/0") == "rediss://:p@h:6379/0?ssl_cert_reqs=none"


# ============================================================================
# F11: L7 Per-tenant Volume Encryption & Integrity Scripts
# ============================================================================


def test_f11_volume_dek_wrap_unwrap_and_policy_enforcement(monkeypatch):
    monkeypatch.setenv("SMSLY_VOLUME_KEK", "e2e-test-volume-kek")
    raw_dek, wrapped, key_id = volume_encryption.generate_wrapped_dek()
    assert len(raw_dek) == 32
    assert len(key_id) == 12
    assert volume_encryption.unwrap_dek(wrapped) == raw_dek

    with pytest.raises(volume_encryption.VolumeEncryptionError):
        volume_encryption.unwrap_dek("tampered-fernet-token")

    monkeypatch.setenv("SMSLY_VOLUME_ENCRYPTION_POLICY", "required")
    addon = SimpleNamespace(provider_metadata={})
    with patch.object(
        volume_encryption,
        "preflight",
        return_value={"available": False, "backend": None, "reason": "no luks/fscrypt"},
    ):
        with pytest.raises(volume_encryption.VolumeEncryptionError):
            volume_encryption.prepare_volume(addon, "vol-req")


def test_f11_volume_encryption_redacts_wrapped_dek_from_sanitized_status(db, user):
    svc = Service.objects.create(name="f11-redact-svc", owner=user)
    addon = Addon.objects.create(
        service=svc,
        name="pg-enc-f11",
        addon_type="POSTGRES",
        status=Addon.Status.ACTIVE,
        provider_metadata={
            "volume_encryption": {
                "encrypted": True,
                "backend": "luks",
                "status": "encrypted_active",
                "key_id": "deadbeef1234",
                "wrapped_dek": "SHOULD-NEVER-LEAK",
            }
        },
    )
    sanitized = volume_encryption.get_sanitized_status(addon)
    assert sanitized["encrypted"] is True
    assert sanitized["key_id"] == "deadbeef1234"
    assert "wrapped_dek" not in sanitized


def test_f11_volume_model_clean_rejects_docker_sock_and_sensitive_paths(db, user):
    from django.core.exceptions import ValidationError

    svc = Service.objects.create(name="f11-vol-model", owner=user)
    bad = Volume(service=svc, name="badvol", mount_path="/var/run/docker.sock", size_gb=1)
    with pytest.raises(ValidationError):
        bad.clean()

    good = Volume(service=svc, name="appdata", mount_path="/data/app", size_gb=5)
    good.clean()


def test_f11_volume_crypt_script_syntax_and_integrity_script_guards():
    proc = run_wsl_bash_check("bash -n scripts/smsly-volume-crypt.sh && bash -n scripts/verify_platform_integrity.sh")
    assert proc.returncode == 0, proc.stderr

    helper = repo_path("scripts/smsly-volume-crypt.sh").read_text(encoding="utf-8")
    assert "cryptsetup luksFormat" in helper
    assert "fscrypt encrypt" in helper
    assert "${mnt}/data" in helper
    assert "timeout -k 5" in helper

    integrity = repo_path("scripts/verify_platform_integrity.sh").read_text(encoding="utf-8")
    assert "ensure_encrypted_volumes_healthy" in integrity


@pytest.mark.parametrize(
    "compose_rel",
    [
        "infrastructure/docker/docker-compose.node.yml",
        "docker-compose.prod.yml",
        "infrastructure/docker/docker-compose.falco.yml",
    ],
)
def test_f9_falco_uses_modern_ebpf_and_pinned_version(compose_rel):
    data = load_compose_yaml(compose_rel)
    falco = data["services"]["falco"]
    assert "0.45.2" in falco["image"]
    assert "modern_ebpf" in " ".join(falco["command"])
    assert falco["deploy"]["resources"]["limits"]["memory"] == "${FALCO_MEMORY_LIMIT:-1024M}"


def test_f9_falco_memory_cap_covers_no_low_default_regression():
    node_text = repo_path("infrastructure/docker/docker-compose.node.yml").read_text(encoding="utf-8")
    assert "FALCO_MEMORY_LIMIT:-256M" not in node_text
    prod_text = repo_path("docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "FALCO_MEMORY_LIMIT:-512M" not in prod_text
    assert repo_path(".env.example").read_text(encoding="utf-8").count("FALCO_MEMORY_LIMIT=1024M") == 1


def test_f10_shared_postgres_enforces_ssl_and_upgrades_legacy_container(tmp_path, monkeypatch):
    monkeypatch.setenv("SMSLY_ADDON_CA_DIR", str(tmp_path))
    cmds = []

    def _fake_run(cmd, timeout=60):
        cmds.append(cmd)
        m = MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "ps", "-a"]:
            m.stdout = "old123\n"
        elif cmd[:2] == ["docker", "inspect"] and ".Mounts" in " ".join(cmd):
            m.stdout = "/var/lib/postgresql/data "
        elif cmd[:2] == ["docker", "inspect"] and ".NetworkSettings.Networks" in " ".join(cmd):
            m.stdout = '{"smsly-net": {"Aliases": ["smsly-shared-postgres"]}}'
        elif cmd[:2] == ["docker", "ps"]:
            m.stdout = "new456\n"
        return m

    with patch("apps.addons.services.addon_tls.ensure_tls_volume", return_value="shared-tls"), patch(
        "apps.addons.services.shared_postgres._superuser_password", return_value="spw"
    ), patch("apps.addons.services.shared_postgres._wait_ready"), patch(
        "apps.addons.services.shared_postgres._psql", return_value=""
    ), patch("apps.addons.services.shared_postgres._psql_on", return_value=""), patch(
        "apps.addons.services.shared_postgres._run", side_effect=_fake_run
    ):
        shared_postgres.ensure_shared_server()

    flat = [" ".join(c) for c in cmds]
    run_cmd = next(c for c in flat if c.startswith("docker run -d"))
    assert "ssl=on" in run_cmd
    assert f"{shared_postgres.SHARED_TLS_VOLUME}:/etc/ssl/smsly-addon:ro" in run_cmd
    assert any("hostssl" in c for c in flat)


def test_f11_volume_encryption_disabled_still_fails_closed_for_existing_encrypted_volume(monkeypatch):
    monkeypatch.setenv("SMSLY_VOLUME_ENCRYPTION_POLICY", "disabled")
    addon = SimpleNamespace(
        provider_metadata={
            "volume_encryption": {
                "encrypted": True,
                "backend": "luks",
                "wrapped_dek": "bad-token",
                "key_id": "deadbeef0000",
            }
        }
    )
    with pytest.raises(volume_encryption.VolumeEncryptionError):
        volume_encryption.verify_or_unlock(addon, "vol-existing-enc")


def test_f11_volume_encryption_rejects_invalid_volume_name_characters():
    for bad_name in ("../../etc/passwd", "bad volume", "vol;rm -rf /", ""):
        with pytest.raises(volume_encryption.VolumeEncryptionError):
            volume_encryption.validate_volume_name(bad_name)
    assert volume_encryption.validate_volume_name("smsly-addon-pg-1-data") == "smsly-addon-pg-1-data"


@pytest.mark.parametrize(
    "sslmode",
    ["disable", "allow", "prefer", "require", "verify-ca"],
)
def test_f10_ensure_pg_ssl_url_enforces_required_minimum(sslmode):
    res = addon_tls.ensure_pg_ssl_url(f"postgresql://u:p@h:5432/db?sslmode={sslmode}")
    assert "sslmode=disable" not in res
    assert "sslmode=allow" not in res
    assert "sslmode=prefer" not in res
    if sslmode in ("require", "verify-ca"):
        assert f"sslmode={sslmode}" in res
    else:
        assert "sslmode=require" in res

