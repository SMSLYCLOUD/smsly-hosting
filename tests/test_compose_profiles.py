"""Regression: full is the default compose profile set (run everything).

Guards the 2026-09 full-default change against silent revert:
  - docker-compose.prod.yml service/profile wiring
  - lib/ defaults (fresh_config, common, platform-env, install_tier)
  - backend/install.sh bundle parity for the same hunks

Stdlib only (no yaml dependency) — parses the compose file as text.
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def service_block(compose, name):
    """Return the raw text block for `  <name>:` service (to next service)."""
    m = re.search(rf"^  {re.escape(name)}:\n", compose, re.M)
    if not m:
        return ""
    start = m.start()
    nxt = re.search(r"^  [a-zA-Z0-9_-]+:\n", compose[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(compose)
    return compose[start:end]


class TestComposeProfiles(unittest.TestCase):
    def test_db_modes_are_exclusive(self):
        compose = read("docker-compose.prod.yml")
        for svc, want in (
            ("db", "local-ha"),
            ("postgres-replica", "local-ha"),
            ("etcd", "patroni"),
            ("patroni1", "patroni"),
            ("haproxy", "patroni"),
        ):
            block = service_block(compose, svc)
            self.assertIn(f'"{want}"', block, f"{svc} must be {want}-gated")

    def test_observability_is_medium_full(self):
        compose = read("docker-compose.prod.yml")
        for svc in (
            "loki",
            "promtail",
            "grafana",
            "cadvisor",
            "docker-labels",
            "alertmanager",
            "prometheus",
            "node-exporter",
        ):
            block = service_block(compose, svc)
            self.assertIn('"medium"', block, f"{svc} must include medium")
            self.assertIn('"full"', block, f"{svc} must include full")

    def test_full_services_are_full_gated(self):
        compose = read("docker-compose.prod.yml")
        for svc in (
            "falco",
            "spire-server",
            "spire-server-ecosystem",
            "apt-cacher",
            "verdaccio",
            "appsec-agent",
            "appsec-envoy",
            "appsec-shared-storage",
            "appsec-smartsync",
            "appsec-tuning-svc",
            "appsec-db",
        ):
            block = service_block(compose, svc)
            self.assertIn('profiles: ["full"]', block, f"{svc} must be full-gated")

    def test_spire_agents_never_start_via_profiles(self):
        # Single-use join tokens can't live in compose (AGENTS.md #18).
        compose = read("docker-compose.prod.yml")
        for svc in ("spire-agent", "spire-agent-ecosystem"):
            block = service_block(compose, svc)
            self.assertIn('profiles: ["manual"]', block, f"{svc} must be manual-only")
            self.assertIn('restart: "no"', block, f"{svc} must not restart")

    def test_lib_defaults_include_full(self):
        fresh = read("lib/fresh_config.sh")
        self.assertIn('COMPOSE_PROFILES="${COMPOSE_PROFILES},full"', fresh)
        common = read("lib/common.sh")
        self.assertIn('COMPOSE_PROFILES="local-ha,medium,full"', common)
        platform_env = read("lib/platform-env.sh")
        self.assertIn('"${_db_ha_mode},medium,full"', platform_env)
        tier = read("scripts/install_tier.sh")
        self.assertIn('TIER="${1:-full}"', tier)
        self.assertIn('PROFILES="$DB_HA,medium,full"', tier)

    def test_fresh_template_writes_full(self):
        fresh = read("lib/fresh_config.sh")
        # Computation appends ,medium and ,full; the heredoc template then
        # writes the computed value.
        self.assertIn('*) COMPOSE_PROFILES="${COMPOSE_PROFILES},full"', fresh)
        self.assertIn("COMPOSE_PROFILES=$COMPOSE_PROFILES", fresh)

    def test_bundle_parity(self):
        bundle = read("backend/install.sh")
        self.assertIn('docker compose -p smsly-hosting -f "$spire_file"', bundle)
        self.assertIn("smsly-hosting_spire-ecosystem-agent-socket", bundle)
        self.assertNotIn('docker compose -p smsly-spire -f "$spire_file"', bundle)
        self.assertIn("-p smsly-hosting", bundle)
        self.assertIn("FULL_MISSING", bundle)
        # Fresh computes profiles dynamically; bundle must carry the same
        # computation (not a stale literal).
        self.assertIn('*) COMPOSE_PROFILES="${COMPOSE_PROFILES},full"', bundle)
        self.assertIn('"${_db_ha_mode},medium,full"', bundle)

    def test_bundle_harden_openappsec(self):
        # The bundle predated the WAF submodule split and silently skipped
        # the whole open-appsec lifecycle (2026-09). Every harden.sh copy
        # must carry the nested block; use scripts/regen-bundle.sh to
        # re-converge after touching lib/harden*.sh.
        bundle = read("backend/install.sh")
        self.assertEqual(
            len(re.findall(r"^# --- lib/harden_openappsec\.sh ---$", bundle, re.M)),
            4,
        )
        self.assertEqual(bundle.count("_harden_openappsec_reconcile()"), 4)
        self.assertIn("_harden_openappsec_reconcile || true", bundle)

    def test_bundle_lib_sync_p1(self):
        # lib/ fixes must be mirrored into the standalone bundle or
        # curl-pipe installs silently miss them (use
        # scripts/regen-bundle.sh to re-converge after touching lib/).
        # Floors, not exact counts: nested copies fan out on regen.
        # scripts/regen-bundle.sh --check (CI gate) covers exact sync.
        bundle = read("backend/install.sh")
        floors = {
            'token="$(timeout 30 docker exec "$server"': 4,
            "Aborting: continuing would leave registry": 1,
            "reload-or-restart fail2ban": 1,
            "systemctl start smsly-memory-tuning.service": 1,
            "Re-apply ownership after any resume reconcile": 1,
            "backups_data|caddy_data|caddy_logs": 2,
            "Domain state sync timed out (non-fatal)": 2,
        }
        for snippet, minimum in floors.items():
            self.assertGreaterEqual(
                bundle.count(snippet), minimum, snippet)


if __name__ == "__main__":
    unittest.main()
