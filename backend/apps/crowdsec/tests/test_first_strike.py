"""Unit tests for the CrowdSec first-strike sync (no DB, no docker)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.crowdsec.tasks import (
    FIRST_STRIKE_SCENARIOS,
    _build_override_content,
    _first_strike_config,
    _first_strike_filename,
    _first_strike_scenario_name,
    _pin_capacity,
    crowdsec_first_strike_sync,
)

HUB_YAML = """type: leaky
name: crowdsecurity/http-sensitive-files
filter: 'evt.Meta.log_type in ["http_access-log"]'
groupby: "evt.Meta.source_ip"
capacity: 4
leakspeed: 5s
labels:
  remediation: true
"""


def _result(returncode=0, stdout="", stderr=""):
    res = MagicMock()
    res.returncode = returncode
    res.stdout = stdout
    res.stderr = stderr
    return res


def _run_sync(docker_fake, enabled=True):
    with patch(
        "apps.crowdsec.tasks._first_strike_config", return_value=enabled
    ), patch(
        "apps.crowdsec.tasks._docker", side_effect=docker_fake
    ):
        return crowdsec_first_strike_sync.run()


class TestFirstStrikeHelpers(TestCase):
    def test_name_mapping(self):
        self.assertEqual(
            _first_strike_scenario_name("crowdsecurity/http-sensitive-files"),
            "smsly/http-sensitive-files-first-strike",
        )
        self.assertEqual(
            _first_strike_filename("crowdsecurity/http-sensitive-files"),
            "http-sensitive-files-first-strike.yaml",
        )

    def test_pin_capacity(self):
        pinned = _pin_capacity(HUB_YAML, 1)
        self.assertIn("\ncapacity: 1\n", pinned)
        self.assertNotIn("capacity: 4", pinned)
        # Filters/data untouched.
        self.assertIn("groupby:", pinned)

    def test_pin_capacity_absent_returns_none(self):
        self.assertIsNone(_pin_capacity("type: trigger\nname: x\n", 1))

    def test_scope_covers_exploit_scenarios_only(self):
        self.assertIn("crowdsecurity/http-sensitive-files", FIRST_STRIKE_SCENARIOS)
        self.assertIn(
            "crowdsecurity/http-path-traversal-probing", FIRST_STRIKE_SCENARIOS
        )
        for excluded in (
            "crowdsecurity/http-probing",
            "crowdsecurity/http-crawl-non_statics",
            "crowdsecurity/http-generic-bf",
        ):
            self.assertNotIn(excluded, FIRST_STRIKE_SCENARIOS)

    def test_config_defaults_on(self):
        with patch(
            "apps.deployments.models.PlatformConfig.load",
            side_effect=RuntimeError("no db"),
        ):
            self.assertTrue(_first_strike_config())


class TestFirstStrikeSync(TestCase):
    def _hub_list(self, installed=True):
        import json

        names = (
            list(FIRST_STRIKE_SCENARIOS)
            if installed
            else []
        )
        return json.dumps(
            {"scenarios": [{"name": n, "status": "enabled"} for n in names]}
        )

    def test_enabled_installs_overrides_and_removes_originals(self):
        calls = []

        def fake(*cmd, **kwargs):
            cmd = list(cmd)
            cmd = list(cmd)
            calls.append(cmd)
            if cmd[:2] == ["exec", "smsly-crowdsec"]:
                sub = cmd[2:]
                if sub[:2] == ["cscli", "scenarios"] and sub[2] == "list":
                    return _result(stdout=self._hub_list(installed=True))
                if sub[0] == "ls":
                    return _result(stdout="CVE-2017-9841.yaml\n")
                if sub[0] == "cat":
                    return _result(stdout=HUB_YAML)
                if sub[:2] == ["cscli", "scenarios"]:
                    return _result(stdout="ok")
                if sub == ["crowdsec", "-t", "-c", "/etc/crowdsec/config.yaml"]:
                    return _result(stdout="test done")
                return _result(stdout="")
            if cmd[:2] == ["kill"]:
                return _result(stdout="")
            if cmd[0] == "cp":
                return _result(stdout="")
            return _result(stdout="")

        res = _run_sync(fake, enabled=True)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["changed"])
        self.assertTrue(res["reloaded"])
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("scenarios remove" in c for c in flat))
        self.assertTrue(any(c.startswith("kill") for c in flat))

    def test_enabled_converged_is_noop(self):
        def fake(*cmd, **kwargs):
            cmd = list(cmd)
            if cmd[:2] == ["exec", "smsly-crowdsec"]:
                sub = cmd[2:]
                if sub[:2] == ["cscli", "scenarios"] and sub[2] == "list":
                    return _result(stdout=self._hub_list(installed=False))
                if sub[0] == "ls":
                    return _result(
                        stdout="http-sensitive-files-first-strike.yaml\n"
                        "http-admin-interface-probing-first-strike.yaml\n"
                        "http-path-traversal-probing-first-strike.yaml\n"
                    )
                if sub[0] == "cat":
                    # Existing override already equals desired content.
                    from apps.crowdsec.tasks import _build_override_content

                    path = sub[1]
                    if "hub/scenarios" in path:
                        return _result(stdout=HUB_YAML)
                    hub = next(
                        h for h in FIRST_STRIKE_SCENARIOS
                        if path.endswith(
                            h.split("/", 1)[1] + "-first-strike.yaml"
                        )
                    )
                    return _result(
                        stdout=_build_override_content(hub, HUB_YAML, 1)
                    )
            return _result(stdout="")

        res = _run_sync(fake, enabled=True)
        self.assertEqual(res["status"], "ok")
        self.assertFalse(res["changed"])
        self.assertFalse(res["reloaded"])

    def test_disabled_restores_originals(self):
        calls = []

        def fake(*cmd, **kwargs):
            cmd = list(cmd)
            calls.append(cmd)
            if cmd[:2] == ["exec", "smsly-crowdsec"]:
                sub = cmd[2:]
                if sub[:2] == ["cscli", "scenarios"] and sub[2] == "list":
                    return _result(stdout=self._hub_list(installed=False))
                if sub[0] == "ls":
                    return _result(
                        stdout="http-sensitive-files-first-strike.yaml\n"
                    )
                if sub[0] in ("rm",):
                    return _result(stdout="")
                if sub[:2] == ["cscli", "scenarios"]:
                    return _result(stdout="ok")
                if sub == ["crowdsec", "-t", "-c", "/etc/crowdsec/config.yaml"]:
                    return _result(stdout="test done")
                return _result(stdout="")
            if cmd[:2] == ["kill"]:
                return _result(stdout="")
            return _result(stdout="")

        res = _run_sync(fake, enabled=False)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["changed"])
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("scenarios install" in c for c in flat))

    def test_build_override_content_guards_missing_capacity(self):
        self.assertIsNone(_build_override_content("x/y", "type: trigger\n", 1))

    def test_write_override_makes_file_world_readable(self):
        """Regression (2026-09-14): backend runs as uid 1000, so a default
        0600 temp file arrives unreadable in the CrowdSec container."""
        import os

        from apps.crowdsec import tasks as tasks_module

        seen = {}

        def fake_chmod(path, mode):
            seen["mode"] = oct(mode)

        def fake(*cmd, **kwargs):
            cmd = list(cmd)
            return _result(stdout="")

        with patch.object(
            tasks_module, "_docker", side_effect=fake
        ), patch("os.chmod", side_effect=fake_chmod):
            self.assertTrue(
                tasks_module._write_override_file("x-first-strike.yaml", "capacity: 1\n")
            )
        self.assertEqual(seen.get("mode"), "0o644")
