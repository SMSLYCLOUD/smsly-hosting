"""Unit tests for Cloudflare Rulesets v4 bouncer fast-lane logic."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

_SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "cf_bouncer_rulesets.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("cf_bouncer_rulesets_test", str(_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["cf_bouncer_rulesets_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class CfBouncerFastLaneTests(unittest.TestCase):
    @unittest.skipUnless(_SCRIPT.exists(), "scripts/cf_bouncer_rulesets.py not present in container image")
    def test_build_fast_lane_ips_excludes_known_and_orders_newest(self):
        cf = _load_mod()
        active = {"198.51.100.10": 100.0, "198.51.100.20": 200.0, "198.51.100.30": 150.0}
        known = {"198.51.100.20": "item-1"}
        res = cf.build_fast_lane_ips(active, known)
        self.assertEqual(res, ("198.51.100.10", "198.51.100.30"))

    @unittest.skipUnless(_SCRIPT.exists(), "scripts/cf_bouncer_rulesets.py not present in container image")
    def test_sync_fast_lane_creates_patches_and_deletes(self):
        cf = _load_mod()
        zone_state = {"zoneA": {"ruleset_id": "rs1", "fast_rule_id": None}}
        calls = []

        def fake_http(url, *, method="GET", headers=None, payload=None, timeout=20):
            calls.append((method, url, payload))
            return {"result": {"rules": [{"id": "rule-fast-1", "description": cf.FAST_LANE_DESC}]}}

        with patch.object(cf, "http_json", side_effect=fake_http), patch.object(cf.time, "sleep"):
            cf.sync_fast_lane_rules(zone_state, ("198.51.100.77",), "block", {"Authorization": "x"})
            self.assertEqual(zone_state["zoneA"]["fast_rule_id"], "rule-fast-1")
            self.assertEqual(calls[-1][0], "POST")
            self.assertEqual(calls[-1][2]["expression"], "(ip.src in {198.51.100.77})")

            cf.sync_fast_lane_rules(zone_state, ("198.51.100.77", "198.51.100.78"), "block", {"Authorization": "x"})
            self.assertEqual(calls[-1][0], "PATCH")
            self.assertTrue(calls[-1][1].endswith("/rules/rule-fast-1"))

            cf.sync_fast_lane_rules(zone_state, (), "block", {"Authorization": "x"})
            self.assertEqual(calls[-1][0], "DELETE")
            self.assertIsNone(zone_state["zoneA"]["fast_rule_id"])
