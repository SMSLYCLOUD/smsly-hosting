"""E2E security tests for Layer 1 Edge WAF hardening (F1 Coraza bypass & F2 open-appsec)."""
from __future__ import annotations

import builtins
import os
import posixpath
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import pytest

from tests.e2e_security.helpers import (
    extract_caddy_site_blocks,
    extract_caddy_snippet,
    repo_path,
    run_wsl_bash_check,
    sign_gateway_v2,
)

pytestmark = [pytest.mark.security]


# ---------------------------------------------------------------------------
# Helpers local to L1 WAF tests
# ---------------------------------------------------------------------------


def _assert_strips_remote_sync_before_coraza(block: str, label: str = "") -> None:
    strip = "request_header -X-SMSLY-Remote-Sync"
    imp = "import coraza_waf"
    assert "route {" in block, f"Expected route block in {label or 'site block'}"
    assert strip in block, f"Expected X-SMSLY-Remote-Sync strip in {label or 'site block'}"
    assert imp in block, f"Expected import coraza_waf in {label or 'site block'}"
    route_pos = block.index("route {")
    strip_pos = block.index(strip)
    coraza_pos = block.index(imp)
    assert route_pos < strip_pos < coraza_pos, (
        f"Expected route < strip < coraza ordering in {label or 'site block'}"
    )
    if "reverse_proxy" in block[route_pos:]:
        proxy_pos = block.index("reverse_proxy", route_pos)
        assert coraza_pos < proxy_pos, (
            f"Expected import coraza_waf before reverse_proxy in {label or 'site block'}"
        )


def _mock_docker_ps_and_appsec_run(policy_yaml: str = "mode: prevent\n"):
    def _resp(stdout: str = "", returncode: int = 0):
        m = MagicMock()
        m.stdout = stdout
        m.stderr = ""
        m.returncode = returncode
        return m

    def _run(cmd, **kwargs):
        joined = " ".join(cmd)
        if cmd[:2] == ["aa-status", "--enabled"]:
            return _resp("", returncode=1)
        if cmd[:2] == ["docker", "info"]:
            return _resp('["name=apparmor","name=seccomp,profile=builtin"]\n')
        if "docker" in cmd[0] and "ps" in cmd:
            return _resp("smsly-appsec-agent Up 1 hour\nsmsly-appsec-envoy Up 1 hour\n")
        if "port" in cmd and "smsly-appsec-envoy" in joined:
            return _resp("8081/tcp -> 127.0.0.1:18081\n")
        if "exec" in cmd and "local_policy.yaml" in joined:
            return _resp(policy_yaml)
        if "logs" in cmd and "smsly-appsec-envoy" in joined:
            return _resp("got final verict: 1\n")
        if "logs" in cmd and "smsly-falco" in joined:
            return _resp("falco running\n")
        if "inspect" in cmd:
            return _resp("0\n")
        return _resp("", returncode=1)

    return _run


def _setup_multi_tenant_platform(user):
    from apps.cloud.models import CloudProvider
    from apps.deployments.models import Deployment, ManagedServer, Service
    from apps.deployments.models.addons import Addon
    from apps.deployments.models.core import PlatformConfig
    from apps.domains.models import Domain, DomainStatus

    PlatformConfig.clear_cache()
    config, _ = PlatformConfig.objects.update_or_create(
        id=1,
        defaults={
            "domain": "trulay.site",
            "use_ssl": True,
            "wildcard_subdomains": True,
            "openappsec_enabled": True,
            "openappsec_mode": "prevent",
        },
    )
    primary = ManagedServer.objects.create(
        owner=user,
        name="primary-master",
        host="192.0.2.10",
        is_primary=True,
        status="ACTIVE",
    )
    remote_node = ManagedServer.objects.create(
        owner=user,
        name="worker-node1",
        host="198.51.100.20",
        node_number=1,
        wg_address="10.100.0.2",
        status="ACTIVE",
    )
    local_provider = CloudProvider.objects.create(
        name="local-l1-test",
        provider_type=CloudProvider.ProviderType.LOCAL,
        is_active=True,
    )
    remote_provider = CloudProvider.objects.create(
        name="remote-l1-test",
        provider_type=CloudProvider.ProviderType.REMOTE,
        is_active=True,
    )
    tenant = Service.objects.create(
        name="tenant-web",
        owner=user,
        provider=local_provider,
        server=primary,
        status=Service.Status.ACTIVE,
        public_domain="tenant-web.trulay.site",
        staging_domain="staging.tenant-web.example.com",
        waf_opt_out=False,
    )
    optout = Service.objects.create(
        name="optout-web",
        owner=user,
        provider=local_provider,
        server=primary,
        status=Service.Status.ACTIVE,
        public_domain="optout-web.trulay.site",
        waf_opt_out=True,
    )
    remote_svc = Service.objects.create(
        name="remote-web",
        owner=user,
        provider=remote_provider,
        server=remote_node,
        status=Service.Status.ACTIVE,
        node_url_enabled=True,
        public_domain="remote-web.trulay.site",
        internal_port=8000,
        waf_opt_out=False,
    )
    Domain.objects.create(
        service=tenant,
        domain_name="custom.tenant.example.com",
        status=DomainStatus.ACTIVE,
        verified=True,
    )
    Deployment.objects.create(
        service=tenant,
        status=Deployment.Status.STAGED,
        staging_url="https://staging.tenant-web.example.com",
        commit_hash="deadbeef",
    )
    Addon.objects.create(
        service=tenant,
        name="addon-pg",
        addon_type="POSTGRES",
        public_domain="db.tenant.example.com",
    )
    return config, remote_node, tenant, optout, remote_svc


# ===========================================================================
# F1: L1 Coraza WAF Bypass Scoping
# ===========================================================================


class TestF1CorazaWafBypassTier1:
    """F1 Tier 1 — Positive & Boundary Contracts."""

    def test_f1_t1_coraza_snippet_scopes_bypass_to_api_v1_with_normalization(self):
        from apps.deployments.services.caddy_manager import config_generation as cg

        snippet = extract_caddy_snippet(cg.CORAZA_SNIPPET, "coraza_waf")
        assert snippet.startswith("(coraza_waf) {")
        assert "load_owasp_crs" in snippet
        assert "Include @coraza.conf-recommended" in snippet
        assert "Include @crs-setup.conf.example" in snippet
        assert "Include @owasp_crs/*.conf" in snippet
        assert "SecRuleEngine On" in snippet

        rule_match = re.search(
            r'SecRule\s+REQUEST_HEADERS:X-SMSLY-Remote-Sync\s+"@streq 1"\s+"([^"]+)"\s*\n\s*'
            r'SecRule\s+REQUEST_URI\s+"@beginsWith /api/v1/"\s+"([^"]+)"',
            snippet,
        )
        assert rule_match is not None, "Expected chained id:1000001 rule scoping X-SMSLY-Remote-Sync to /api/v1/"
        head_actions, tail_actions = rule_match.group(1), rule_match.group(2)
        assert "id:1000001" in head_actions
        assert "chain" in head_actions
        assert "ctl:ruleEngine=Off" not in head_actions
        assert "t:none" in tail_actions
        assert "t:urlDecodeUni" in tail_actions
        assert "t:normalizePath" in tail_actions
        assert "ctl:ruleEngine=Off" in tail_actions

    def test_f1_t1_generated_master_caddyfile_passes_all_validators(self, user):
        from apps.deployments.services.caddy_manager import config_generation as cg
        from apps.deployments.services.caddy_manager import validation as cv

        config, *_ = _setup_multi_tenant_platform(user)
        caddyfile = cg.generate_caddyfile(config)

        assert extract_caddy_snippet(caddyfile, "coraza_waf")
        assert "order coraza_waf first" in caddyfile
        assert cv.validate_edge_sidecar_endpoints(caddyfile) == []
        assert cv.validate_secure_headers_present(caddyfile) == []
        assert cv.validate_service_routes_do_not_hit_control_plane(caddyfile) == []
        assert cv.validate_wildcard_redirects_authoritative(caddyfile) == []

    def test_f1_t1_tenant_blocks_strip_remote_sync_before_coraza(self, user):
        from apps.deployments.services.caddy_manager import config_generation as cg

        config, *_ = _setup_multi_tenant_platform(user)
        caddyfile = cg.generate_caddyfile(config)
        blocks = extract_caddy_site_blocks(caddyfile)

        for domain_label in (
            "custom.tenant.example.com",
            "staging.tenant-web.example.com",
            "db.tenant.example.com",
        ):
            assert domain_label in blocks, f"Missing site block for {domain_label}"
            _assert_strips_remote_sync_before_coraza(blocks[domain_label], domain_label)

        alias_block = cg._build_host_alias_block(
            "alias.tenant.example.com",
            "/login",
            "http://127.0.0.1:8000",
            "tenant-web.trulay.site",
        )
        _assert_strips_remote_sync_before_coraza(alias_block, "host_alias")

        wildcard_block = blocks["*.trulay.site"]
        known_idx = wildcard_block.index("handle @known_hosts {")
        known_section = wildcard_block[known_idx : known_idx + 450]
        _assert_strips_remote_sync_before_coraza(known_section, "wildcard @known_hosts")

    def test_f1_t1_waf_optout_hosts_strip_remote_sync_and_precede_known_hosts(self, user):
        from apps.deployments.services.caddy_manager import config_generation as cg

        config, *_ = _setup_multi_tenant_platform(user)
        caddyfile = cg.generate_caddyfile(config)
        blocks = extract_caddy_site_blocks(caddyfile)

        wildcard_block = blocks["*.trulay.site"]
        assert "@waf_optout" in wildcard_block
        assert wildcard_block.index("@waf_optout") < wildcard_block.index("@known_hosts host")
        optout_idx = wildcard_block.index("handle @waf_optout {")
        optout_section = wildcard_block[optout_idx : wildcard_block.index("handle @known_hosts {")]
        assert "request_header -X-SMSLY-Remote-Sync" in optout_section
        assert "import coraza_waf" not in optout_section

        svc_optout = cg._build_service_domain_block(
            "optout.example.com", "optout.example.com", waf_opt_out=True
        )
        assert "request_header -X-SMSLY-Remote-Sync" in svc_optout
        assert "import coraza_waf" not in svc_optout

        alias_optout = cg._build_host_alias_block(
            "alias-optout.example.com", "/login", "http://127.0.0.1:8000", "optout.example.com",
            waf_opt_out=True,
        )
        assert "request_header -X-SMSLY-Remote-Sync" in alias_optout
        assert "import coraza_waf" not in alias_optout

    def test_f1_t1_control_plane_preserves_remote_sync_while_node_tenants_strip(self, user):
        from apps.deployments.services.caddy_manager import config_generation as cg

        config, remote_node, *_ = _setup_multi_tenant_platform(user)
        master_cf = cg.generate_caddyfile(config)
        master_blocks = extract_caddy_site_blocks(master_cf)

        assert "trulay.site" in master_blocks
        assert "request_header -X-SMSLY-Remote-Sync" not in master_blocks["trulay.site"]
        assert "import coraza_waf" in master_blocks["*.trulay.site"]
        assert "request_header -X-SMSLY-Remote-Sync" in master_blocks["*.trulay.site"]

        node_cf = cg.generate_node_caddyfile(remote_node)
        node_blocks = extract_caddy_site_blocks(node_cf)

        assert "grid1.trulay.site" in node_blocks
        assert "import coraza_waf" in node_blocks["grid1.trulay.site"]
        assert "request_header -X-SMSLY-Remote-Sync" not in node_blocks["grid1.trulay.site"]

        assert "request_header -X-SMSLY-Remote-Sync" in node_blocks["remote-web-grid1.trulay.site:80"]
        _assert_strips_remote_sync_before_coraza(
            node_blocks["remote-web.grid1.trulay.site"], "remote-web.grid1.trulay.site"
        )
        _assert_strips_remote_sync_before_coraza(
            node_blocks["remote-web-node1.trulay.site"], "remote-web-node1.trulay.site"
        )
        _assert_strips_remote_sync_before_coraza(
            node_blocks["remote-web.trulay.site"], "remote-web.trulay.site"
        )

    def test_f1_t1_security_status_reports_coraza_protection_counts(
        self, api_client, superuser, tmp_path, monkeypatch
    ):
        from apps.deployments.services.caddy_manager import config_generation as cg

        config, *_ = _setup_multi_tenant_platform(superuser)
        caddyfile = cg.generate_caddyfile(config)
        (tmp_path / "Caddyfile").write_text(caddyfile, encoding="utf-8")
        monkeypatch.setenv("CADDY_CONFIG_DIR", str(tmp_path))

        api_client.force_authenticate(user=superuser)
        with patch(
            "apps.core.views.security.subprocess.run",
            side_effect=_mock_docker_ps_and_appsec_run("mode: prevent\n"),
        ):
            resp = api_client.get("/api/v1/system/security-status/")

        assert resp.status_code == 200
        coraza = resp.data["coraza"]
        assert coraza["snippet_present"] is True
        assert coraza["site_imports"] >= 1
        assert coraza["services_protected"] == 2
        assert coraza["services_opted_out"] == 1


class TestF1CorazaWafBypassTier2:
    """F1 Tier 2 — Negative, Bypass & Adversarial Boundary Contracts."""

    def test_f1_t2_no_unchained_global_remote_sync_bypass(self):
        from apps.deployments.services.caddy_manager import config_generation as cg

        snippet = extract_caddy_snippet(cg.CORAZA_SNIPPET, "coraza_waf")
        assert snippet.count("`") == 2
        # Every ctl:ruleEngine=Off rule must appear only as the chained URI rule
        off_lines = [
            line.strip()
            for line in snippet.splitlines()
            if "ctl:ruleEngine=Off" in line and not line.strip().startswith("#")
        ]
        assert len(off_lines) == 1
        assert "REQUEST_URI" in off_lines[0]
        assert "@beginsWith /api/v1/" in off_lines[0]
        assert "X-SMSLY-Remote-Sync" not in off_lines[0]

    def test_f1_t2_path_traversal_and_prefix_collision_do_not_match_api_v1(self):
        from apps.deployments.services.caddy_manager import config_generation as cg

        snippet = extract_caddy_snippet(cg.CORAZA_SNIPPET, "coraza_waf")
        assert "t:urlDecodeUni" in snippet
        assert "t:normalizePath" in snippet
        assert 'SecRule REQUEST_URI "@beginsWith /api/v1/"' in snippet

        def _coraza_normalized_uri(raw_uri: str) -> str:
            decoded = unquote(unquote(raw_uri))
            norm = posixpath.normpath(decoded)
            if decoded.endswith("/") and not norm.endswith("/"):
                norm += "/"
            return norm

        for adversarial_uri in (
            "/api/v1evil",
            "/api/v2/users",
            "/api/v1",
            "/admin/",
            "/api/v1/%2e%2e/admin/",
            "/api/v1/..%2fadmin/users",
        ):
            normalized = _coraza_normalized_uri(adversarial_uri)
            assert not normalized.startswith("/api/v1/"), (
                f"Adversarial URI {adversarial_uri!r} normalized to {normalized!r} unexpectedly matched /api/v1/"
            )

    def test_f1_t2_apply_caddyfile_refuses_missing_coraza_snippet_or_order(
        self, tmp_path, monkeypatch
    ):
        from apps.deployments.services.caddy_manager import apply as caddy_apply
        from apps.deployments.services.caddy_manager import config_generation as cg

        monkeypatch.setattr(caddy_apply, "_last_caddy_reload_ts", 0.0)
        monkeypatch.setattr(caddy_apply, "_last_caddy_content_hash", "")
        monkeypatch.setattr(caddy_apply, "CADDY_FILE_PATH", str(tmp_path / "Caddyfile"))
        monkeypatch.setattr(caddy_apply, "CADDY_CONFIG_DIR", str(tmp_path))

        missing_snippet_cf = "\n".join([
            "{",
            "    order coraza_waf first",
            "}",
            cg.SECURE_HEADERS_SNIPPET,
            "example.com {",
            "    import secure_headers",
            "    reverse_proxy 127.0.0.1:8000",
            "}",
        ])
        with patch.object(caddy_apply, "caddy_disabled_mode", return_value=False):
            res_missing_snippet = caddy_apply.apply_caddyfile(missing_snippet_cf)
        assert res_missing_snippet["ok"] is False
        assert "coraza_waf" in res_missing_snippet["message"].lower()

        missing_order_cf = "\n".join([
            "{",
            "    admin 0.0.0.0:2019",
            "}",
            cg.SECURE_HEADERS_SNIPPET,
            cg.CORAZA_SNIPPET,
            "example.com {",
            "    import secure_headers",
            "    import coraza_waf",
            "    reverse_proxy 127.0.0.1:8000",
            "}",
        ])
        with patch.object(caddy_apply, "caddy_disabled_mode", return_value=False):
            res_missing_order = caddy_apply.apply_caddyfile(missing_order_cf)
        assert res_missing_order["ok"] is False
        assert "order coraza_waf" in res_missing_order["message"].lower()

    def test_f1_t2_tenant_wildcard_and_remote_upstream_hosts_strip_header(self, user):
        from apps.deployments.services.caddy_manager import config_generation as cg

        config, *_ = _setup_multi_tenant_platform(user)
        caddyfile = cg.generate_caddyfile(config)
        blocks = extract_caddy_site_blocks(caddyfile)

        wildcard_block = blocks["*.trulay.site"]
        first_handle_idx = wildcard_block.index("handle ")
        preamble = wildcard_block[:first_handle_idx]
        assert "request_header -X-SMSLY-Remote-Sync" in preamble, (
            "Wildcard block must strip X-SMSLY-Remote-Sync top-level before any handle block"
        )
        assert "remote-web.trulay.site" in wildcard_block

    def test_f1_t2_tenant_api_v1_route_cannot_bypass_coraza_with_remote_sync(self, user):
        from apps.deployments.services.caddy_manager import config_generation as cg

        config, remote_node, *_ = _setup_multi_tenant_platform(user)
        master_cf = cg.generate_caddyfile(config)
        master_blocks = extract_caddy_site_blocks(master_cf)
        node_cf = cg.generate_node_caddyfile(remote_node)
        node_blocks = extract_caddy_site_blocks(node_cf)

        # Even if attacker hits https://tenant/api/v1/login with X-SMSLY-Remote-Sync: 1,
        # tenant route strips the header BEFORE import coraza_waf evaluates rule 1000001.
        _assert_strips_remote_sync_before_coraza(
            master_blocks["custom.tenant.example.com"], "custom domain"
        )
        wildcard = master_blocks["*.trulay.site"]
        known_section = wildcard[wildcard.index("handle @known_hosts {") :]
        _assert_strips_remote_sync_before_coraza(known_section, "wildcard @known_hosts")
        _assert_strips_remote_sync_before_coraza(
            node_blocks["remote-web.trulay.site"], "node tenant domain"
        )

    def test_f1_t2_unauthenticated_security_status_rejected(self, api_client):
        no_hmac = api_client.get("/api/v1/system/security-status/")
        assert no_hmac.status_code == 403

        headers = {
            f"HTTP_{k.upper().replace('-', '_')}": v
            for k, v in sign_gateway_v2("GET", "/api/v1/system/security-status/").items()
        }
        with_hmac = api_client.get("/api/v1/system/security-status/", **headers)
        assert with_hmac.status_code == 401


# ===========================================================================
# F2: L1 open-appsec Default 'prevent' Mode
# ===========================================================================


class TestF2OpenAppsecPreventTier1:
    """F2 Tier 1 — Positive & Boundary Contracts."""

    def test_f2_t1_platform_config_defaults_openappsec_prevent_and_enabled(self, db):
        from apps.deployments.models.platform import PlatformConfig

        fresh = PlatformConfig()
        assert fresh.openappsec_enabled is True
        assert fresh.openappsec_mode == "prevent"

        PlatformConfig.clear_cache()
        loaded = PlatformConfig.load()
        assert loaded.openappsec_enabled is True
        assert loaded.openappsec_mode == "prevent"

    def test_f2_t1_domain_config_api_reports_and_toggles_openappsec_mode(
        self, api_client, superuser
    ):
        from apps.deployments.models.platform import PlatformConfig

        api_client.force_authenticate(user=superuser)
        with patch("apps.domains.views.domain._crowdsec_cf_bouncer_running", return_value=False):
            get_resp = api_client.get("/api/v1/system/domain-config/")
        assert get_resp.status_code == 200
        assert get_resp.data["openappsec_enabled"] is True
        assert get_resp.data["openappsec_mode"] == "prevent"

        with patch("apps.deployments.signals.infrastructure.apply_caddyfile"), patch(
            "apps.deployments.signals.infrastructure.generate_caddyfile", return_value=""
        ), patch(
            "apps.deployments.services.caddy_manager.apply_caddyfile",
            return_value={"ok": True, "message": "Applied (mocked)"},
        ), patch(
            "apps.deployments.services.caddy_manager.generate_caddyfile",
            return_value="",
        ):
            put_learn = api_client.put(
                "/api/v1/system/domain-config/",
                {"openappsec_mode": "detect-learn"},
                format="json",
            )
            assert put_learn.status_code == 200
            PlatformConfig.clear_cache()
            assert PlatformConfig.load().openappsec_mode == "detect-learn"

            put_prevent = api_client.put(
                "/api/v1/system/domain-config/",
                {"openappsec_mode": "prevent"},
                format="json",
            )
            assert put_prevent.status_code == 200
            PlatformConfig.clear_cache()
            assert PlatformConfig.load().openappsec_mode == "prevent"

    def test_f2_t1_security_status_reports_openappsec_prevent_and_shadow_port(
        self, api_client, superuser
    ):
        api_client.force_authenticate(user=superuser)
        with patch(
            "apps.core.views.security.subprocess.run",
            side_effect=_mock_docker_ps_and_appsec_run("mode: prevent\n"),
        ):
            resp = api_client.get("/api/v1/system/security-status/")

        assert resp.status_code == 200
        oa = resp.data["openappsec"]
        assert oa["enabled"] is True
        assert oa["agent_running"] is True
        assert oa["envoy_running"] is True
        assert oa["mode_configured"] == "prevent"
        assert oa["policy_mode"] == "prevent"
        assert oa["shadow_port"] == 18081
        assert oa["verdicts_recent"] is True

    def test_f2_t1_sync_waf_policy_converges_drift_to_prevent(self, db):
        from apps.deployments.models.platform import PlatformConfig
        from apps.deployments.tasks.infra import tasks_waf

        cfg = PlatformConfig.load()
        cfg.openappsec_enabled = True
        cfg.openappsec_mode = "prevent"
        cfg.save(update_fields=["openappsec_enabled", "openappsec_mode"])

        policy_state = {
            "text": (
                "policies:\n"
                "  default:\n"
                "    mode: detect-learn\n"
                "    override-mode: detect-learn\n"
                "    access-control:\n"
                "      mode: response-code-only\n"
            )
        }

        container = MagicMock()

        def _exec_run(cmd):
            if cmd[:2] == ["cat", tasks_waf.AGENT_POLICY_PATH]:
                return (0, policy_state["text"].encode("utf-8"))
            if cmd[:3] == ["sed", "-i", "-E"]:
                # Simulate exact top-level mode rewrite without touching override-mode or nested mode
                policy_state["text"] = re.sub(
                    r"^([ \t]*mode:[ \t]*)(detect-learn|prevent)([ \t]*)$",
                    r"\1prevent\3",
                    policy_state["text"],
                    flags=re.MULTILINE,
                )
                return (0, b"")
            return (1, b"")

        container.exec_run.side_effect = _exec_run
        mock_client = MagicMock()
        mock_client.containers.get.return_value = container

        with patch("docker.from_env", return_value=mock_client):
            result = tasks_waf.sync_waf_policy_task()

        assert result == {"ok": True, "changed": True, "previous": "detect-learn", "mode": "prevent"}
        container.restart.assert_called_once()
        assert "    mode: prevent\n" in policy_state["text"]
        assert "override-mode: detect-learn" in policy_state["text"]
        assert "mode: response-code-only" in policy_state["text"]

    def test_f2_t1_shell_desired_mode_and_env_defaults_prevent(self, tmp_path):
        script_path = repo_path("lib", "harden_openappsec.sh")
        # Convert Windows path to /mnt/<drive>/... when invoked via wsl from Windows
        wsl_lib = f"/mnt/{script_path.drive[0].lower()}{script_path.as_posix()[2:]}" if script_path.drive else str(script_path)
        empty_dir = tmp_path / "empty_install"
        empty_dir.mkdir()
        wsl_empty = f"/mnt/{empty_dir.drive[0].lower()}{empty_dir.as_posix()[2:]}" if empty_dir.drive else str(empty_dir)

        proc_default = run_wsl_bash_check(
            f"unset OPENAPPSEC_MODE; INSTALL_DIR='{wsl_empty}'; "
            f"source '{wsl_lib}'; _harden_openappsec_desired_mode"
        )
        assert proc_default.returncode == 0, proc_default.stderr
        assert proc_default.stdout.strip() == "prevent"

        proc_learn = run_wsl_bash_check(
            f"OPENAPPSEC_MODE=detect-learn INSTALL_DIR='{wsl_empty}' "
            f"bash -c 'source \"{wsl_lib}\" && _harden_openappsec_desired_mode'"
        )
        assert proc_learn.returncode == 0, proc_learn.stderr
        assert proc_learn.stdout.strip() == "detect-learn"

        env_example = repo_path(".env.example").read_text(encoding="utf-8")
        assert re.search(r"^OPENAPPSEC_MODE=prevent\s*$", env_example, re.MULTILINE)
        assert re.search(r"^OPENAPPSEC_SHADOW_HTTP_PORT=18081\s*$", env_example, re.MULTILINE)

        platform_env = repo_path("lib", "platform-env.sh").read_text(encoding="utf-8")
        assert 'env_ensure_var "$env_file" "OPENAPPSEC_MODE" "prevent"' in platform_env

        install_sh = repo_path("backend", "install.sh").read_text(encoding="utf-8")
        assert 'env_ensure_var "$env_file" "OPENAPPSEC_MODE" "prevent"' in install_sh

    def test_f2_t1_platform_config_save_syncs_openappsec_mode_to_env(self, db, tmp_path):
        from apps.deployments.models.platform import PlatformConfig

        fake_env = tmp_path / ".env"
        fake_env.write_text("DOMAIN=old.example.com\nOPENAPPSEC_MODE=detect-learn\n", encoding="utf-8")

        real_open = builtins.open
        real_isfile = os.path.isfile
        real_access = os.access

        def _open_proxy(file, *args, **kwargs):
            if file == "/app/.env":
                return real_open(fake_env, *args, **kwargs)
            return real_open(file, *args, **kwargs)

        def _isfile_proxy(path):
            if path == "/app/.env":
                return True
            if path == "/caddy-config/.env":
                return False
            return real_isfile(path)

        def _access_proxy(path, mode):
            if path == "/app/.env":
                return real_access(fake_env, mode)
            return real_access(path, mode)

        PlatformConfig.clear_cache()
        cfg = PlatformConfig.load()
        cfg.domain = "hardened.example.com"
        cfg.openappsec_enabled = True
        cfg.openappsec_mode = "prevent"

        with patch("builtins.open", side_effect=_open_proxy), patch(
            "os.path.isfile", side_effect=_isfile_proxy
        ), patch("os.access", side_effect=_access_proxy), patch(
            "apps.deployments.signals.infrastructure.apply_caddyfile"
        ), patch(
            "apps.deployments.signals.infrastructure.generate_caddyfile", return_value=""
        ), patch(
            "apps.deployments.signals.infrastructure.patch_runtime_settings"
        ):
            cfg.save()

        synced = fake_env.read_text(encoding="utf-8")
        assert "OPENAPPSEC_MODE=prevent\n" in synced
        assert "OPENAPPSEC_ENABLED=1\n" in synced


class TestF2OpenAppsecPreventTier2:
    """F2 Tier 2 — Negative, Boundary & Fail-Closed Contracts."""

    def test_f2_t2_domain_config_rejects_invalid_openappsec_mode(self, api_client, superuser):
        from apps.deployments.models.platform import PlatformConfig

        api_client.force_authenticate(user=superuser)
        for bad_mode in ("block", "off", "", "PREVENT", "shadow"):
            resp = api_client.put(
                "/api/v1/system/domain-config/",
                {"openappsec_mode": bad_mode},
                format="json",
            )
            assert resp.status_code == 400, f"Expected 400 for mode={bad_mode!r}, got {resp.status_code}"
        PlatformConfig.clear_cache()
        assert PlatformConfig.load().openappsec_mode == "prevent"

    def test_f2_t2_non_admin_cannot_read_or_modify_domain_config(self, api_client, user):
        api_client.force_authenticate(user=user)
        assert api_client.get("/api/v1/system/domain-config/").status_code == 403
        put_resp = api_client.put(
            "/api/v1/system/domain-config/",
            {"openappsec_mode": "detect-learn"},
            format="json",
        )
        assert put_resp.status_code == 403

    def test_f2_t2_sync_waf_policy_skips_unknown_policy_schema_safely(self, db):
        from apps.deployments.models.platform import PlatformConfig
        from apps.deployments.tasks.infra import tasks_waf

        cfg = PlatformConfig.load()
        cfg.openappsec_enabled = True
        cfg.openappsec_mode = "prevent"
        cfg.save(update_fields=["openappsec_enabled", "openappsec_mode"])

        container = MagicMock()
        container.exec_run.return_value = (
            0,
            b"override-mode: detect-learn\npractices:\n  - name: custom\n",
        )
        mock_client = MagicMock()
        mock_client.containers.get.return_value = container

        with patch("docker.from_env", return_value=mock_client):
            result = tasks_waf.sync_waf_policy_task()

        assert result["ok"] is True
        assert result["skipped"] is True
        container.exec_run.assert_called_once()
        container.restart.assert_not_called()

    def test_f2_t2_sync_waf_policy_no_restart_when_already_prevent(self, db):
        from apps.deployments.models.platform import PlatformConfig
        from apps.deployments.tasks.infra import tasks_waf

        cfg = PlatformConfig.load()
        cfg.openappsec_enabled = True
        cfg.openappsec_mode = "prevent"
        cfg.save(update_fields=["openappsec_enabled", "openappsec_mode"])

        container = MagicMock()
        container.exec_run.return_value = (0, b"mode: prevent\n")
        mock_client = MagicMock()
        mock_client.containers.get.return_value = container

        with patch("docker.from_env", return_value=mock_client):
            result = tasks_waf.sync_waf_policy_task()

        assert result == {"ok": True, "changed": False, "mode": "prevent"}
        container.exec_run.assert_called_once()
        container.restart.assert_not_called()

    def test_f2_t2_normalize_desired_mode_fails_closed_to_prevent(self):
        from apps.deployments.tasks.infra.tasks_waf import normalize_desired_mode

        for raw in ("", None, "invalid", "DETECT-LEARN", "enforce", "shadow"):
            assert normalize_desired_mode(raw) == "prevent"
        assert normalize_desired_mode("detect-learn") == "detect-learn"

        script_path = repo_path("lib", "harden_openappsec.sh")
        wsl_lib = f"/mnt/{script_path.drive[0].lower()}{script_path.as_posix()[2:]}" if script_path.drive else str(script_path)
        proc = run_wsl_bash_check(
            f"OPENAPPSEC_MODE='DETECT-LEARN' INSTALL_DIR='/tmp/nonexistent-smsly' "
            f"bash -c 'source \"{wsl_lib}\" && _harden_openappsec_desired_mode'"
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == "prevent"

    def test_f2_t2_db_disabled_openappsec_overrides_env_flag(
        self, api_client, superuser, monkeypatch
    ):
        from apps.deployments.models.platform import PlatformConfig
        from apps.deployments.tasks.infra import tasks_waf

        cfg = PlatformConfig.load()
        cfg.openappsec_enabled = False
        cfg.save(update_fields=["openappsec_enabled"])
        monkeypatch.setenv("OPENAPPSEC_ENABLED", "1")

        api_client.force_authenticate(user=superuser)
        with patch(
            "apps.core.views.security.subprocess.run",
            side_effect=_mock_docker_ps_and_appsec_run("mode: prevent\n"),
        ):
            resp = api_client.get("/api/v1/system/security-status/")
        assert resp.status_code == 200
        assert resp.data["openappsec"]["enabled"] is False

        with patch("docker.from_env") as mock_docker:
            sync_res = tasks_waf.sync_waf_policy_task()
        assert sync_res["skipped"] is True
        mock_docker.assert_not_called()
