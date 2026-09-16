"""Regression: idle-minimal, burst-allowed resource scaling.

Guards the 2026-09 scaling change:
  - celery-worker-autoscaler manages REAL burst services (celery-fast,
    celery-deploy), never phantom celery-2/3 (silent no-op before).
  - The primary worker drains all queues, so idle-stop can't stall work.
  - No healer fights scale-down (monitor + ensure + refresh paths).
  - Autoscaler budgets against host RAM, not a hardcoded 10GB box.
  - Fresh installs size workers/buffers from hardware; updates backfill.
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def compose_services():
    compose = read("docker-compose.prod.yml")
    return set(re.findall(r"^  ([a-zA-Z0-9_-]+):\n", compose, re.M))


class TestBurstTopology(unittest.TestCase):
    def test_autoscaler_targets_exist_in_compose(self):
        script = read("scripts/celery-worker-autoscaler.sh")
        m = re.search(r"BURST_WORKERS=\(([^)]+)\)", script)
        self.assertIsNotNone(m, "BURST_WORKERS list not found")
        targets = m.group(1).split()
        self.assertEqual(sorted(targets), ["celery-deploy", "celery-fast"])
        services = compose_services()
        for svc in targets:
            self.assertIn(svc, services, f"burst target {svc} missing from compose")

    def test_no_phantom_worker_refs(self):
        # Code references only (comments may mention history). A live
        # `celery-2` reference would be a silent no-op: no such service
        # exists in docker-compose.prod.yml.
        for rel in (
            "scripts/celery-worker-autoscaler.sh",
            "lib/docker.sh",
            "scripts/monitor_infra.sh",
        ):
            code = "\n".join(
                line for line in read(rel).splitlines()
                if not line.strip().startswith("#")
            )
            self.assertNotIn("celery-2", code, f"{rel} references phantom celery-2")
            self.assertNotIn("celery-3", code, f"{rel} references phantom celery-3")
            self.assertNotIn("extra-workers", code, f"{rel} references missing profile")

    def test_main_worker_drains_all_queues(self):
        # The safety premise of idle-stop: the always-on worker covers
        # every queue the burst workers serve.
        fresh = read("lib/fresh_config.sh")
        self.assertIn('ENV_CELERY_QUEUES="${CELERY_QUEUES:-celery,fast,deploy}"', fresh)
        self.assertIn("CELERY_QUEUES=$ENV_CELERY_QUEUES", fresh)
        platform_env = read("lib/platform-env.sh")
        self.assertIn('"CELERY_QUEUES" "celery,fast,deploy"', platform_env)

    def test_monitor_exempts_burst_when_autoscaled(self):
        monitor = read("scripts/monitor_infra.sh")
        self.assertIn("AUTOSCALED_SERVICES=(", monitor)
        self.assertIn('"celery-fast" "celery-deploy"', monitor)
        # Burst services must NOT be in the always-heal list anymore.
        prod = re.search(r"PROD_SERVICES=\((.*?)\)", monitor, re.S).group(1)
        self.assertNotIn("celery-fast", prod)
        self.assertNotIn("celery-deploy", prod)
        self.assertIn("AUTOSCALE_ON", monitor)

    def test_ensure_enforces_mandatory_only(self):
        docker = read("lib/docker.sh")
        self.assertIn("local mandatory=(celery celery-beat)", docker)
        self.assertIn("local burst=(celery-deploy celery-fast)", docker)


class TestHostAwareSizing(unittest.TestCase):
    def test_autoscaler_detects_host_ram(self):
        auto = read("scripts/smsly-autoscaler.py")
        self.assertIn("MemTotal", auto)
        self.assertIn("_detect_host_total_mb", auto)
        self.assertIn("_TOTAL_FALLBACK_MB", auto)

    def test_fresh_sizes_from_hardware(self):
        fresh = read("lib/fresh_config.sh")
        for key in ("ENV_GUNICORN_WORKERS", "ENV_DB_SHARED_BUFFERS",
                    "ENV_DB_EFFECTIVE_CACHE_SIZE", "ENV_CELERY_QUEUES"):
            self.assertIn(key, fresh, f"{key} not computed in fresh_config")
        for key in ("GUNICORN_WORKERS=$ENV_GUNICORN_WORKERS",
                    "DB_SHARED_BUFFERS=$ENV_DB_SHARED_BUFFERS",
                    "DB_EFFECTIVE_CACHE_SIZE=$ENV_DB_EFFECTIVE_CACHE_SIZE",
                    "CELERY_QUEUES=$ENV_CELERY_QUEUES"):
            self.assertIn(key, fresh, f"template missing {key}")

    def test_platform_env_backfills_sizing(self):
        platform_env = read("lib/platform-env.sh")
        for key in ("GUNICORN_WORKERS", "DB_SHARED_BUFFERS",
                    "DB_EFFECTIVE_CACHE_SIZE", "CELERY_QUEUES",
                    "CELERY_AUTOSCALE_ENABLED"):
            self.assertIn(f'"{key}"', platform_env, f"backfill missing {key}")

    def test_sizing_thresholds_pinned(self):
        # The 256/512MB buffer ladder and 2/4 worker split are the idle
        # contract: changing them silently re-sizes every fresh install.
        fresh = read("lib/fresh_config.sh")
        for literal in ("ENV_DB_SHARED_BUFFERS=256MB", "ENV_DB_SHARED_BUFFERS=512MB",
                        "ENV_DB_SHARED_BUFFERS=1GB", "ENV_GUNICORN_WORKERS=2",
                        "ENV_GUNICORN_WORKERS=4", "-le 4096", "-le 8192"):
            self.assertIn(literal, fresh, f"sizing literal missing: {literal}")
        platform_env = read("lib/platform-env.sh")
        for literal in ("_want_buffers=256MB", "_want_buffers=512MB",
                        "_want_buffers=1GB", "_want_workers=2", "_want_workers=4"):
            self.assertIn(literal, platform_env, f"backfill literal missing: {literal}")

    def test_scale_down_guards_inflight_work(self):
        script = read("scripts/celery-worker-autoscaler.sh")
        self.assertIn("messages_unacknowledged", script)
        self.assertIn("_get_burst_unacked", script)
        # Blind ticks (broker down) must skip, never act.
        self.assertIn("skipping tick (no scale action blind)", script)

    def test_scaler_resolves_managed_workers(self):
        # Agent-lite/node compose files have no burst services; acting on
        # a missing service fails, and under pipefail that exits the loop
        # into a systemd crash-loop. Must resolve + exit cleanly instead.
        script = read("scripts/celery-worker-autoscaler.sh")
        self.assertIn("_resolve_managed_workers", script)
        self.assertIn("MANAGED_WORKERS", script)
        self.assertIn("nothing to manage. Exiting.", script)
        self.assertIn('"${MANAGED_WORKERS[@]}"', script)

    def test_no_stale_scaler_naming_in_installer(self):
        verify = read("lib/fresh_verify.sh")
        code = "\n".join(
            line for line in verify.splitlines()
            if not line.strip().startswith("#")
        )
        self.assertNotIn("celery-2", code)
        self.assertNotIn("celery-3", code)


class TestStaticCaps(unittest.TestCase):
    def test_falco_has_memory_cap(self):
        compose = read("docker-compose.prod.yml")
        falco = compose[compose.index("\n  falco:"):compose.index("\n  spire-server:")]
        self.assertIn("FALCO_MEMORY_LIMIT", falco)

    def test_prometheus_retention_parameterized(self):
        for rel in ("docker-compose.prod.yml",
                    "infrastructure/docker/docker-compose.observability.yml"):
            content = read(rel)
            self.assertIn("${PROMETHEUS_RETENTION:-30d}", content, rel)
            self.assertNotIn("retention.time=30d'", content.replace(
                "--storage.tsdb.retention.time=${PROMETHEUS_RETENTION:-30d}'", ""), rel)

    def test_loki_retention_parameterized(self):
        # Loki retention is a knob like Prometheus (set both together on
        # small hosts) — not a hardcoded literal that drifts silently.
        cfg = read("infrastructure/monitoring/loki-config.yml")
        self.assertIn("${LOKI_RETENTION:-30d}", cfg)
        for rel in ("docker-compose.prod.yml",
                    "infrastructure/docker/docker-compose.observability.yml"):
            content = read(rel)
            self.assertIn("-config.expand-env=true", content, rel)
            self.assertIn("LOKI_RETENTION: ${LOKI_RETENTION:-30d}", content, rel)

    def test_env_knobs_live_in_example_template_and_backfill(self):
        # A tunable that exists only as a compose `:-` default is
        # undiscoverable and unpinned. Retention/caps/secrets must be
        # documented (.env.example), written (fresh template), and
        # backfilled (platform-env) together.
        example = read(".env.example")
        fresh = read("lib/fresh_config.sh")
        backfill = read("lib/platform-env.sh")
        for key in ("PROMETHEUS_RETENTION", "LOKI_RETENTION",
                    "FALCO_MEMORY_LIMIT"):
            self.assertIn("\n%s" % key, example, key)
            self.assertIn(key, fresh, key)
            self.assertIn('"%s"' % key, backfill, key)
        # COSIGN_PASSWORD is written by the fresh template and backfilled
        # on the update/recovery path (not platform-env) — pin each home.
        self.assertIn("\nCOSIGN_PASSWORD=", example)
        self.assertIn("COSIGN_PASSWORD=$COSIGN_PASSWORD", fresh)
        self.assertIn("COSIGN_PASSWORD", read("lib/update_preflight.sh"))
        self.assertIn("COSIGN_PASSWORD", read("lib/ops_recovery.sh"))
        for key in ("OPENAPPSEC_ENVOY_MEMORY_LIMIT",
                    "OPENAPPSEC_STORAGE_MEMORY_LIMIT",
                    "OPENAPPSEC_DB_MEMORY_LIMIT"):
            self.assertIn(key, example, key)

    def test_scaler_resolves_broker_container(self):
        # The rabbitmq service has no container_name: (runtime name is
        # smsly-hosting-rabbitmq-1), so `docker exec rabbitmq` never
        # resolves and the scaler goes permanently blind. Probes must go
        # through compose ps -q.
        script = read("scripts/celery-worker-autoscaler.sh")
        self.assertNotIn("docker exec rabbitmq ", script)
        self.assertIn('docker compose -f "$COMPOSE_FILE" ps -q rabbitmq', script)

    def test_no_unbounded_service(self):
        # Invariant (not anecdote): EVERY service in the prod compose file
        # must carry a memory limit. patroni-db-init was the last miss;
        # the old assertion only pinned one variable name and would have
        # passed with any future uncapped service.
        compose = read("docker-compose.prod.yml")
        services_start = compose.index("\nservices:")
        tail = compose[services_start:]
        end = re.search(r"(?m)^[a-z][a-z_-]*:\s*$", tail[len("\nservices:"):])
        svc_text = tail[:len("\nservices:") + end.start()] if end else tail
        parts = re.split(r"(?m)^  ([a-zA-Z0-9_-]+):\n", svc_text)
        names = parts[1::2]
        self.assertGreater(len(names), 40)  # sanity: parser sees the fleet
        missing = [
            names[i] for i in range(len(names))
            if "memory:" not in parts[2 * i + 2]
        ]
        self.assertEqual(missing, [])

    def test_waf_envoy_trimmed_for_shadow_phase(self):
        # The shadow serves loopback probes only (see
        # infrastructure/openappsec/envoy.yaml) — half a core / 512M is
        # ~5-10x its idle footprint. Docker-enforced, no vendor semantics.
        compose = read("docker-compose.prod.yml")
        envoy = compose[compose.index("\n  appsec-envoy:"):compose.index("\n  appsec-shared-storage:")]
        self.assertIn("${OPENAPPSEC_ENVOY_MEMORY_LIMIT:-512M}", envoy)
        self.assertIn('cpus: "0.50"', envoy)
        # Thread-count tuning stays vendor-default: the entrypoint contract
        # for CONCURRENCY_* isn't documented in-tree; guessing risks the
        # filter. Revisit with measurements at phase-2 cutover.
        self.assertIn("- CONCURRENCY_CALC=numOfCores", envoy)

    def test_waf_agent_floor_pinned(self):
        # The agent is a vendor binary with resident signatures — its 2G
        # cap is a floor, not fat. Trim only from measured p95 (see the
        # runbook in .env.example), never by guessing here.
        compose = read("docker-compose.prod.yml")
        agent = compose[compose.index("\n  appsec-agent:"):compose.index("\n  appsec-envoy:")]
        self.assertIn("${OPENAPPSEC_AGENT_MEMORY_LIMIT:-2G}", agent)


class TestDefaultsOn(unittest.TestCase):
    """Everything that can be on by default is on by default.

    Only credentials/external-input gates stay off (SSL certs, wildcard
    DNS token, SMTP, Sentry, AI/billing/OAuth keys, Mapbox, CrowdSec
    enrollment) — everything else ships enabled.
    """

    def test_waf_shadow_default_on(self):
        example = read(".env.example")
        self.assertIn("\nOPENAPPSEC_ENABLED=1\n", example)
        harden = read("lib/harden_openappsec.sh")
        self.assertIn('"${OPENAPPSEC_ENABLED:-1}"', harden)

    def test_waf_default_is_size_aware(self):
        # The shadow costs ~1-2GB real: on at >=8GB, off below, explicit
        # operator values always win on both paths.
        fresh = read("lib/fresh_config.sh")
        self.assertIn("OPENAPPSEC_ENABLED=$ENV_OPENAPPSEC_ENABLED", fresh)
        self.assertIn("ENV_OPENAPPSEC_ENABLED=", fresh)
        self.assertIn("-ge 8192", fresh)
        platform_env = read("lib/platform-env.sh")
        self.assertIn("OPENAPPSEC_ENABLED", platform_env)
        self.assertIn("-ge 8192", platform_env)

    def test_waf_size_boundary_behavior(self):
        # The literal above is not enough: `-gt 8192` passes a grep for
        # 8192 yet flips every exact-8GB host (shadow costs ~1-2GB).
        # Extract the operator from each implementation and evaluate the
        # boundary pair through it.
        import operator as _op
        ops = {"-ge": _op.ge, "-gt": _op.gt,
               "-le": _op.le, "-lt": _op.lt}
        waf_lines = {
            # The WAF decision lives on these lines (other -ge 8192
            # comparisons — frontend build tiers, sizing ladder — have
            # different semantics and their own pins).
            "lib/fresh_config.sh": [
                ln for ln in read("lib/fresh_config.sh").splitlines()
                if "OPENAPPSEC_ENABLED" in ln and "8192" in ln],
            "lib/platform-env.sh": [
                ln for ln in read("lib/platform-env.sh").splitlines()
                if "_waf_ram_mb" in ln and re.search(r"-(ge|gt|le|lt)\s+8192", ln)],
        }
        for rel, lines in waf_lines.items():
            self.assertEqual(len(lines), 1, "%s: one WAF size line" % rel)
            m = re.search(r"-(ge|gt|le|lt)\s+8192", lines[0])
            self.assertIsNotNone(m, "%s: comparison operator found" % rel)
            decide = ops["-" + m.group(1)]
            self.assertFalse(decide(8191, 8192), "%s: 8191MB must disable WAF" % rel)
            self.assertTrue(decide(8192, 8192), "%s: 8192MB must enable WAF" % rel)

    def test_explicit_off_switch_survives(self):
        # Default-on must not strand operators who opt out: reconcile
        # still converges strays down, and verify still fails closed.
        harden = read("lib/harden_openappsec.sh")
        self.assertIn("_harden_openappsec_reconcile", harden)
        self.assertIn("disabled but containers still running", harden)

    def test_grafana_anon_consistent(self):
        for rel in ("docker-compose.prod.yml",
                    "infrastructure/docker/docker-compose.observability.yml"):
            content = read(rel)
            self.assertIn("${GRAFANA_ANON_ENABLED:-true}", content, rel)

    def test_security_layers_default_on(self):
        platform_env = read("lib/platform-env.sh")
        for key in ("NODE_SECURITY", "NODE_CROWDSEC", "NODE_FALCO",
                    "NODE_SPIRE", "BACKUP_REQUIRE_ENCRYPTION",
                    "SMSLY_DISABLE_TIER_GATES"):
            self.assertIn(f'"{key}"', platform_env)


class TestHostMemoryTuning(unittest.TestCase):
    def test_setup_script_covers_ksm_and_zram(self):
        setup = read("scripts/setup-memory-tuning.sh")
        self.assertIn('KSM_DIR="/sys/kernel/mm/ksm"', setup)
        self.assertIn('"$KSM_DIR/run"', setup)
        self.assertIn("SMSLY_DISABLE_KSM", setup)
        self.assertIn("/dev/zram0", setup)
        self.assertIn("SMSLY_DISABLE_ZRAM", setup)
        # Priority ordering: zram above disk swap, and the script never
        # fails the caller (best-effort on exotic kernels).
        self.assertIn("swapon -p 100", setup)
        self.assertIn("exit 0", setup)

    def test_zram_outranks_disk_swap_everywhere(self):
        for rel in ("lib/preflight.sh", "lib/fresh_hardening.sh"):
            content = read(rel)
            self.assertIn("swapon -p 10", content, f"{rel} must pin disk swap below zram")
            self.assertIn("pri=10", content, f"{rel} must persist the priority")

    def test_hardening_installs_memory_unit(self):
        hardening = read("lib/fresh_hardening.sh")
        self.assertIn("setup-memory-tuning.sh", hardening)
        self.assertIn("smsly-memory-tuning.service", hardening)

    def test_integrity_guards_memory_tuning(self):
        integrity = read("scripts/verify_platform_integrity.sh")
        self.assertIn("ensure_memory_tuning", integrity)

    def test_async_accelerators_pinned(self):
        reqs = read("backend/requirements.txt")
        self.assertRegex(reqs, r"(?m)^uvloop==\d+\.\d+", "uvloop must be pinned")
        self.assertRegex(reqs, r"(?m)^httptools==\d+\.\d+", "httptools must be pinned")


if __name__ == "__main__":
    unittest.main()
