"""Regression tests for the 9 On-Demand feature fixes.

Covers all three On-Demand layers:
  1. Infra tier sleep/wake (ui.conf, smsly-tier.sh STACK_TIER guard, tiers.py)
  2. Sablier scale-to-zero (lite_agent guard, in-place label sync, OOM/crash
     detection, HPA standby + min_replicas=0, Caddy 60s retry, worker sleeper)
  3. Addon on-demand parking (Addon.Status.STOPPED, migration 0259, canonical
     container names, remote node power endpoint, UI status/buttons)
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_ROOT = os.path.join(REPO_ROOT, "backend")


def _read(rel: str) -> str:
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _load_backend_module(mod_name: str, rel_path: str):
    path = os.path.join(BACKEND_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _find_git_bash() -> str | None:
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        shutil.which("bash"),
    ):
        if candidate and os.path.isfile(candidate):
            # Ignore WSL's system32 bash stub if it exists on PATH
            if candidate.lower().endswith(r"\system32\bash.exe"):
                continue
            return candidate
    return None


class TestInfraTierOnDemand(unittest.TestCase):
    def test_ui_tier_disables_timed_auto_sleep(self):
        conf = _read("infrastructure/systemd/tiers/ui.conf")
        self.assertIn("SMSLY_TIER_AUTOSLEEP=1", conf)
        self.assertIn("SMSLY_TIER_IDLE_SECS=0", conf)
        self.assertNotIn("SMSLY_TIER_IDLE_SECS=1800", conf)
        with open(os.path.join(REPO_ROOT, "infrastructure/systemd/tiers/ui.conf"), "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\r\n", raw)

    def test_tiers_allowed_on_host(self):
        tiers = _load_backend_module("smsly_tiers", "apps/deployments/services/tiers.py")
        with mock.patch.dict(os.environ, {"STACK_TIER": "lite", "COMPOSE_PROFILES": ""}, clear=False):
            self.assertFalse(tiers.tier_allowed_on_host("buildcache", env_path="nonexistent.env"))
            self.assertFalse(tiers.tier_allowed_on_host("ui", env_path="nonexistent.env"))
            self.assertFalse(tiers.tier_allowed_on_host("metrics", env_path="nonexistent.env"))
            self.assertTrue(tiers.tier_allowed_on_host("secrets", env_path="nonexistent.env"))
        with mock.patch.dict(os.environ, {"STACK_TIER": "medium", "COMPOSE_PROFILES": "medium"}, clear=False):
            self.assertFalse(tiers.tier_allowed_on_host("buildcache", env_path="nonexistent.env"))
            self.assertTrue(tiers.tier_allowed_on_host("ui", env_path="nonexistent.env"))
            self.assertTrue(tiers.tier_allowed_on_host("metrics", env_path="nonexistent.env"))
        with mock.patch.dict(os.environ, {"STACK_TIER": "medium", "COMPOSE_PROFILES": "medium,build-cache"}, clear=False):
            self.assertTrue(tiers.tier_allowed_on_host("buildcache", env_path="nonexistent.env"))
        with mock.patch.dict(os.environ, {"STACK_TIER": "full", "COMPOSE_PROFILES": "full"}, clear=False):
            self.assertTrue(tiers.tier_allowed_on_host("buildcache", env_path="nonexistent.env"))

    def test_smsly_tier_sh_blocks_incompatible_stack_tier_wakes(self):
        bash_bin = _find_git_bash()
        if not bash_bin:
            self.skipTest("Git Bash unavailable on host")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake docker shim so `tier_up` fails fast if it reaches compose()
            bin_dir = os.path.join(tmpdir, "bin")
            os.makedirs(bin_dir)
            docker_shim = os.path.join(bin_dir, "docker")
            with open(docker_shim, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("#!/usr/bin/env bash\necho DOCKER_CALLED \"$*\"\nexit 0\n")
            os.chmod(docker_shim, 0o755)
            # Also provide a Windows .cmd shim in case Git Bash delegates to PATHEXT
            with open(os.path.join(bin_dir, "docker.cmd"), "w", encoding="utf-8") as fh:
                fh.write("@echo off\necho DOCKER_CALLED %*\nexit /b 0\n")
            with open(os.path.join(tmpdir, "docker-compose.prod.yml"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write("services: {}\n")

            tiers_dir = os.path.join(tmpdir, "tiers.d")
            os.makedirs(tiers_dir)
            for fname in ("buildcache.conf", "ui.conf"):
                shutil.copyfile(
                    os.path.join(REPO_ROOT, "infrastructure/systemd/tiers", fname),
                    os.path.join(tiers_dir, fname),
                )

            env_file = os.path.join(tmpdir, ".env")
            tier_script_win = os.path.join(REPO_ROOT, "scripts/smsly-tier.sh").replace("\\", "/")

            def _posix_path(p: str) -> str:
                s = p.replace("\\", "/")
                if len(s) >= 2 and s[1] == ":":
                    return "/" + s[0].lower() + s[2:]
                return s

            def _run_wake(stack_tier: str, profiles: str, tier: str, force: str = "0"):
                with open(env_file, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(f"STACK_TIER={stack_tier}\nCOMPOSE_PROFILES={profiles}\n")
                cmd = (
                    f'export PATH="{_posix_path(bin_dir)}:$PATH" '
                    f'SMSLY_INSTALL_DIR="{_posix_path(tmpdir)}" '
                    f'SMSLY_TIER_DIR="{_posix_path(tiers_dir)}" '
                    f'SMSLY_TIER_STATE_DIR="{_posix_path(tmpdir)}/state" '
                    f'SMSLY_FORCE_TIER_WAKE="{force}"; '
                    f'bash "{tier_script_win}" up {tier}'
                )
                return subprocess.run(
                    [bash_bin, "-c", cmd],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )

            res_lite = _run_wake("lite", "", "buildcache")
            self.assertNotEqual(res_lite.returncode, 0)
            self.assertIn("excluded on STACK_TIER=lite", res_lite.stderr)
            self.assertNotIn("DOCKER_CALLED", res_lite.stdout)

            res_med = _run_wake("medium", "medium", "buildcache")
            self.assertNotEqual(res_med.returncode, 0)
            self.assertIn("excluded on STACK_TIER=medium", res_med.stderr)

            res_ui_lite = _run_wake("lite", "", "ui")
            self.assertNotEqual(res_ui_lite.returncode, 0)
            self.assertIn("excluded on STACK_TIER=lite", res_ui_lite.stderr)

            res_ui_med = _run_wake("medium", "medium", "ui")
            self.assertEqual(res_ui_med.returncode, 0, res_ui_med.stderr)
            self.assertIn("DOCKER_CALLED", res_ui_med.stdout)

            res_force = _run_wake("lite", "", "buildcache", force="1")
            self.assertEqual(res_force.returncode, 0, res_force.stderr)
            self.assertIn("DOCKER_CALLED", res_force.stdout)


class TestSablierOnDemandFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if BACKEND_ROOT not in sys.path:
            sys.path.insert(0, BACKEND_ROOT)
        cls.traefik_labels = _load_backend_module(
            "smsly_traefik_labels_test",
            "apps/deployments/services/traefik_labels.py",
        )

    def test_service_supports_sablier_rejects_lite_agent(self):
        tl = self.traefik_labels
        lite_svc = types.SimpleNamespace(
            id="svc-1",
            name="lite-app",
            sablier_enabled=True,
            active_target_type="lite_agent",
            server=None,
        )
        ok, reason = tl.service_supports_sablier(lite_svc)
        self.assertFalse(ok)
        self.assertIn("lite_agent", reason)

        svr_lite = types.SimpleNamespace(id="svc-2", name="srv-lite", sablier_enabled=True, server=types.SimpleNamespace(is_lite_agent=True))
        with mock.patch.dict(sys.modules, {"apps.deployments.services.internal_wake": types.SimpleNamespace(service_has_mesh_sidecar=lambda s: False)}):
            ok2, reason2 = tl.service_supports_sablier(svr_lite)
        self.assertFalse(ok2)
        self.assertIn("lite agent", reason2)

        with mock.patch.dict(os.environ, {"MODE_AGENT_LITE": "true"}):
            ok3, reason3 = tl.service_supports_sablier(None)
        self.assertFalse(ok3)
        self.assertIn("agent-lite", reason3)

    def test_sync_sablier_labels_on_dict_toggle_on_and_off(self):
        tl = self.traefik_labels
        fake_iw = types.SimpleNamespace(
            internal_wake_host=lambda n: f"{n}.internal",
            service_has_mesh_sidecar=lambda s: False,
        )
        svc = types.SimpleNamespace(
            id="svc-sync-1",
            name="sync-app",
            sablier_enabled=True,
            sablier_session="15m",
            health_check_path="/healthz",
            active_target_type="local",
            server=types.SimpleNamespace(is_lite_agent=False),
        )
        labels = {
            "traefik.enable": "true",
            "traefik.http.routers.sync-app.rule": "Host(`sync-app.smsly.cloud`)",
            "traefik.http.routers.sync-app.service": "sync-app-service",
            "traefik.http.routers.sync-app.middlewares": "secure-headers-strict@file,sync-app-ratelimit",
            "traefik.http.services.sync-app-service.loadbalancer.server.port": "8000",
            "traefik.http.services.sync-app-service.loadbalancer.healthcheck.path": "/healthz",
        }
        with mock.patch.dict(sys.modules, {"apps.deployments.services.internal_wake": fake_iw}):
            tl.sync_sablier_labels_on_dict(labels, svc)
            self.assertEqual(labels["sablier.enable"], "true")
            self.assertEqual(labels["sablier.group"], "sync-app")
            self.assertEqual(
                labels["traefik.http.middlewares.sync-app-sablier.plugin.sablier.sessionDuration"],
                "15m",
            )
            self.assertIn("sync-app-wakeretry,sync-app-sablier", labels["traefik.http.routers.sync-app.middlewares"])
            self.assertIn("traefik.http.routers.sync-app-internal.rule", labels)
            self.assertNotIn("traefik.http.services.sync-app-service.loadbalancer.healthcheck.path", labels)

            # Toggle OFF cleanly strips all Sablier keys & restores healthcheck
            svc.sablier_enabled = False
            tl.sync_sablier_labels_on_dict(labels, svc)
            self.assertNotIn("sablier.enable", labels)
            self.assertNotIn("sablier.group", labels)
            self.assertNotIn("traefik.http.routers.sync-app-internal.rule", labels)
            self.assertNotIn("sync-app-sablier", labels["traefik.http.routers.sync-app.middlewares"])
            self.assertNotIn("sync-app-wakeretry", labels["traefik.http.routers.sync-app.middlewares"])
            self.assertIn("secure-headers-strict@file", labels["traefik.http.routers.sync-app.middlewares"])
            self.assertEqual(
                labels["traefik.http.services.sync-app-service.loadbalancer.healthcheck.path"],
                "/healthz",
            )

    def test_structural_on_demand_contracts(self):
        # Observability retry wake
        obs = _read("backend/apps/core/views/observability.py")
        self.assertIn('ensure_tier_awake("ui", timeout=60)', obs)

        # Builder tier gating
        builder = _read("backend/apps/cloud/services/builder.py")
        self.assertIn('tier_allowed_on_host("buildcache")', builder)

        # Health monitor checks OOMKilled, ExitCode, sablier_enrolled
        hm = _read("backend/apps/core/services/health_monitor.py")
        self.assertIn("def is_sablier_clean_sleep(", hm)
        self.assertIn("OOMKilled", hm)
        self.assertIn("sablier_enrolled", hm)

        # HPA pipeline skips Sablier standby and clamps min_replicas=0
        pipe = _read("backend/apps/autoscaler/engine/pipeline.py")
        self.assertIn("def _is_sablier_standby(", pipe)
        self.assertIn("eff_min_replicas", pipe)
        rec = _read("backend/apps/autoscaler/engine/reconciler.py")
        self.assertGreaterEqual(rec.count("getattr(locked, 'sablier_enabled', False) is True"), 2)

        # Master Caddy 60s try duration on Sablier + Worker sleeper support
        cg = _read("backend/apps/deployments/services/caddy_manager/config_generation.py")
        self.assertIn("lb_try_duration 60s", cg)
        slp = _read("backend/apps/autoscaler/sleeper.py")
        self.assertIn("_is_worker_node_runtime", slp)
        self.assertIn("_node_access_log_has_recent_traffic", slp)

        # Celery task registration for in-place Sablier recreate
        cel = _read("backend/config/celery.py")
        self.assertIn("apps.deployments.tasks.refresh_service_sablier_task", cel)
        init = _read("backend/apps/deployments/tasks/__init__.py")
        self.assertIn("refresh_service_sablier_task", init)

        # Addon STOPPED status, migration 0259, canonical helper & remote power, UI updates
        addons_model = _read("backend/apps/deployments/models/addons.py")
        self.assertIn("STOPPED = 'STOPPED'", addons_model)
        mig = _read("backend/apps/deployments/migrations/0259_alter_addon_status_stopped.py")
        self.assertIn('"0258_reservedsubdomain_partial_unique"', mig)
        self.assertIn('"STOPPED"', mig)
        crud = _read("backend/apps/addons/views/crud.py")
        self.assertIn("addon_container_name(instance)", crud)
        self.assertIn("power_remote_container", crud)
        self.assertIn("Addon.Status.STOPPED", crud)
        node_exec = _read("backend/apps/deployments/views/node_exec.py")
        self.assertIn('url_path="containers/power"', node_exec)
        scaling_tsx = _read("frontend/src/components/settings/ScalingTab.tsx")
        self.assertIn("min={sablier ? 0 : 1}", scaling_tsx)
        self.assertIn("automatically refreshed in-place when saved", scaling_tsx)
        addons_tab = _read("frontend/src/components/addons/AddonsTab.tsx")
        self.assertIn("addon.status === 'STOPPED'", addons_tab)
        addon_detail = _read("frontend/src/app/addons/[id]/page.tsx")
        self.assertIn("handleStartStop", addon_detail)


if __name__ == "__main__":
    unittest.main()
