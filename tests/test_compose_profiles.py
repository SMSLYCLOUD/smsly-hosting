"""Regression: RAM-aware compose profile tiering + bundle parity.

Guards the resource-diet & auto-tiering contract against silent revert:
  - docker-compose.prod.yml service/profile wiring & depends_on decoupling
  - lib/ tiering (fresh_config, common, platform-env, install_tier, harden*)
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
            ("postgres-replica", "local-ha-replica"),
            ("etcd", "patroni"),
            ("patroni1", "patroni"),
            ("haproxy", "patroni"),
        ):
            block = service_block(compose, svc)
            self.assertIn(f'"{want}"', block, f"{svc} must be {want}-gated")
        replica_block = service_block(compose, "postgres-replica")
        self.assertNotIn('"local-ha"', replica_block)
        self.assertNotIn('"patroni"', replica_block)
        pgcat_block = service_block(compose, "pgcat")
        self.assertIn("DB_REPLICA_HOSTS: ${DB_REPLICA_HOSTS:-}", pgcat_block)
        self.assertNotIn("DB_REPLICA_HOSTS: ${DB_REPLICA_HOSTS:-postgres-replica:5432}", pgcat_block)

    def test_lite_excludes_replicas_sentinels_and_crowdsec(self):
        compose = read("docker-compose.prod.yml")
        for svc in (
            "redis-replica",
            "redis-sentinel-1",
            "redis-sentinel-2",
            "redis-sentinel-3",
        ):
            block = service_block(compose, svc)
            self.assertIn('profiles: ["redis-ha"]', block, f"{svc} must be redis-ha-gated")
        for svc in ("crowdsec", "crowdsec-cloudflare-bouncer"):
            block = service_block(compose, svc)
            self.assertIn('profiles: ["medium", "full"]', block, f"{svc} must be medium/full-gated")
        for svc in ("backend", "celery", "celery-beat", "celery-fast", "celery-deploy"):
            block = service_block(compose, svc)
            self.assertNotIn("redis-sentinel-1:", block, f"{svc} must not hard-depend on redis-sentinel-1")
            self.assertIn("redis-primary:", block, f"{svc} must depend on redis-primary")
        traefik_block = service_block(compose, "traefik")
        dep_match = re.search(r"    depends_on:\n((?:      .*\n)+)", traefik_block)
        self.assertIsNotNone(dep_match, "traefik must have depends_on")
        self.assertNotIn("crowdsec:", dep_match.group(1))

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
        ):
            block = service_block(compose, svc)
            self.assertIn('profiles: ["full"]', block, f"{svc} must be full-gated")

    def test_appsec_services_are_appsec_gated(self):
        compose = read("docker-compose.prod.yml")
        for svc in (
            "appsec-agent",
            "appsec-envoy",
            "appsec-shared-storage",
            "appsec-smartsync",
            "appsec-tuning-svc",
            "appsec-db",
        ):
            block = service_block(compose, svc)
            self.assertIn('profiles: ["appsec"]', block, f"{svc} must be appsec-gated")
        self.assertIn('SMSLY_TIER_PROFILES="appsec"', read("infrastructure/systemd/tiers/appsec.conf"))
        self.assertIn('"--profile", "appsec"', read("backend/apps/deployments/tasks/infra/tasks_waf.py"))

    def test_spire_agents_never_start_via_profiles(self):
        # Single-use join tokens can't live in compose (AGENTS.md #18).
        compose = read("docker-compose.prod.yml")
        for svc in ("spire-agent", "spire-agent-ecosystem"):
            block = service_block(compose, svc)
            self.assertIn('profiles: ["manual"]', block, f"{svc} must be manual-only")
            self.assertIn('restart: "no"', block, f"{svc} must not restart")

    def test_lib_defaults_include_ram_tiering(self):
        fresh = read("lib/fresh_config.sh")
        self.assertIn("-lt 6144", fresh)
        self.assertIn("-lt 12288", fresh)
        self.assertIn("local-ha-replica", fresh)
        self.assertIn("redis-ha", fresh)
        self.assertIn(",appsec", fresh)
        self.assertIn("STACK_TIER=$STACK_TIER", fresh)
        self.assertIn("COMPOSE_PROFILES=$COMPOSE_PROFILES", fresh)

        common = read("lib/common.sh")
        self.assertIn("6144", common)
        self.assertIn("12288", common)
        self.assertIn("local-ha,local-ha-replica,redis-ha,medium", common)

        platform_env = read("lib/platform-env.sh")
        self.assertIn('"STACK_TIER"', platform_env)
        self.assertIn("6144", platform_env)
        self.assertIn("12288", platform_env)
        self.assertIn("local-ha-replica", platform_env)
        self.assertIn("redis-ha", platform_env)
        self.assertIn("appsec", platform_env)

        tier = read("scripts/install_tier.sh")
        self.assertIn('TIER="${1:-auto}"', tier)
        self.assertIn("local-ha-replica", tier)
        self.assertIn("redis-ha", tier)
        self.assertIn("--profile appsec", tier)

    def test_bundle_parity(self):
        bundle = read("backend/install.sh")
        self.assertIn('docker compose -p smsly-hosting -f "$spire_file"', bundle)
        self.assertIn("smsly-hosting_spire-ecosystem-agent-socket", bundle)
        self.assertNotIn('docker compose -p smsly-spire -f "$spire_file"', bundle)
        self.assertIn("-p smsly-hosting", bundle)
        self.assertIn("FULL_MISSING", bundle)
        self.assertIn("STACK_TIER=$STACK_TIER", bundle)
        self.assertIn("6144", bundle)
        self.assertIn("12288", bundle)
        self.assertIn("local-ha-replica", bundle)
        self.assertIn("redis-ha", bundle)
        self.assertIn("_harden_stack_tier", bundle)

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
            'token="$(timeout -k 5 30 docker exec "$server"': 4,
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
