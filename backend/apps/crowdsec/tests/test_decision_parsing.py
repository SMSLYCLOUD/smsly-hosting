"""Unit tests for CrowdSec cscli parsing (no DB, no docker).

Regression cover for the 2026-09-14 incident where the Threat Blocks UI
showed a ban count but no IP: modern `cscli decisions list -o json`
returns alert-shaped items (ban in nested ``decisions[0]``, attacker in
``source`` dict, event detail in ``events[].meta`` as a LIST of
``{key, value}`` pairs with the attacked host as ``target_fqdn``),
which the old flat-shape parser could not read (empty value/host).
"""
from unittest import TestCase

from apps.crowdsec.services import (
    CrowdSecService,
    _meta_to_dict,
    _parse_go_duration,
)


def _live_shape():
    """Fixture replicating the live 2026-09-14 record (trimmed)."""
    return {
        "id": 66,
        "uuid": "4affd8f8-9206-4988-94fa-4d895545006b",
        "scenario": "crowdsecurity/http-sensitive-files",
        "events_count": 5,
        "created_at": "2026-09-14T00:06:57Z",
        "start_at": "2026-09-14T00:06:55Z",
        "simulated": False,
        "source": {
            "ip": "34.39.228.253",
            "cn": "BR",
            "as_name": "GOOGLE-CLOUD-PLATFORM",
            "as_number": "396982",
            "range": "34.32.0.0/11",
        },
        "message": "Ip 34.39.228.253 performed "
        "'crowdsecurity/http-sensitive-files' (5 events)",
        "decisions": [
            {
                "duration": "2h34m39s",
                "id": 285046,
                "origin": "crowdsec",
                "scenario": "crowdsecurity/http-sensitive-files",
                "scope": "Ip",
                "simulated": False,
                "type": "ban",
                "value": "34.39.228.253",
            }
        ],
        "events": [
            {
                "timestamp": "2026-09-14 00:06:55 +0000 UTC",
                "meta": [
                    {"key": "http_path", "value": "/.git/config"},
                    {"key": "http_verb", "value": "GET"},
                    {"key": "http_status", "value": "503"},
                    {"key": "source_ip", "value": "34.39.228.253"},
                    {"key": "target_fqdn", "value": "postgres-a6e2bf4a-46a67f.grid.smsly.cloud"},
                ],
            },
            {
                "timestamp": "2026-09-14 00:06:56 +0000 UTC",
                "meta": [
                    {"key": "http_path", "value": "/.env"},
                    {"key": "source_ip", "value": "34.39.228.253"},
                    {"key": "target_fqdn", "value": "postgres-a6e2bf4a-46a67f.grid.smsly.cloud"},
                ],
            },
        ],
    }


class TestMetaHelpers(TestCase):
    def test_meta_list_to_dict(self):
        meta = _meta_to_dict([
            {"key": "target_fqdn", "value": "a.example.com"},
            {"key": "http_path", "value": "/x"},
        ])
        self.assertEqual(meta["target_fqdn"], "a.example.com")
        self.assertEqual(meta["http_path"], "/x")

    def test_meta_dict_passthrough(self):
        self.assertEqual(_meta_to_dict({"a": "b"}), {"a": "b"})
        self.assertEqual(_meta_to_dict(None), {})
        self.assertEqual(_meta_to_dict("x"), {})

    def test_go_duration(self):
        self.assertEqual(_parse_go_duration("2h34m39s"), 2 * 3600 + 34 * 60 + 39)
        self.assertEqual(_parse_go_duration("45m"), 2700)
        self.assertEqual(_parse_go_duration("20s"), 20)
        self.assertIsNone(_parse_go_duration(""))
        self.assertIsNone(_parse_go_duration(None))
        self.assertIsNone(_parse_go_duration("nonsense"))


class TestNormalizeDecision(TestCase):
    def setUp(self):
        self.svc = CrowdSecService()

    def test_live_shape_parses_ip_and_scenario(self):
        d = self.svc._normalize_decision(_live_shape(), {})
        self.assertEqual(d.value, "34.39.228.253")
        self.assertEqual(d.type, "ban")
        self.assertEqual(d.scope, "Ip")
        self.assertEqual(d.scenario, "crowdsecurity/http-sensitive-files")
        self.assertEqual(d.events_count, 5)

    def test_live_shape_extracts_host_and_paths(self):
        d = self.svc._normalize_decision(_live_shape(), {})
        self.assertEqual(d.host, "postgres-a6e2bf4a-46a67f.grid.smsly.cloud")
        self.assertEqual(d.target_host, d.host)
        self.assertIn("/.git/config", d.paths)
        self.assertIn("/.env", d.paths)

    def test_live_shape_extracts_attacker_detail(self):
        d = self.svc._normalize_decision(_live_shape(), {})
        self.assertEqual(d.source_ip, "34.39.228.253")
        self.assertEqual(d.country, "BR")
        self.assertEqual(d.asn_org, "GOOGLE-CLOUD-PLATFORM")
        self.assertEqual(d.ip_range, "34.32.0.0/11")

    def test_live_shape_computes_expiry(self):
        from datetime import datetime, timezone

        d = self.svc._normalize_decision(_live_shape(), {})
        # duration counts down the remaining TTL: expiry ~= now + 2h34m39s.
        self.assertEqual(d.duration, "2h34m39s")
        end_dt = datetime.fromisoformat(d.end_time)
        remaining = (end_dt - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(
            2 * 3600 + 34 * 60 < remaining <= 2 * 3600 + 34 * 60 + 120,
            remaining,
        )

    def test_flat_shape_still_parses(self):
        d = self.svc._normalize_decision({
            "id": "9", "type": "ban", "scope": "Ip", "value": "1.2.3.4",
            "origin": "crowdsec", "scenario": "http-probing",
            "events_count": 5, "simulated": False,
            "start_time": "2026-09-13T00:00:00Z",
            "end_time": "2026-09-13T04:00:00Z",
        }, {})
        self.assertEqual(d.value, "1.2.3.4")
        self.assertEqual(d.end_time, "2026-09-13T04:00:00Z")

    def test_host_attribution(self):
        from unittest.mock import patch

        host_map = {"svc-1": {"postgres-a6e2bf4a-46a67f.grid.smsly.cloud"}}
        svc = CrowdSecService()
        d = svc._normalize_decision(_live_shape(), host_map)
        with patch.object(CrowdSecService, "_get_service_hosts", return_value=host_map):
            enriched = svc._enrich_with_service([d])
        self.assertEqual(enriched[0].service, "svc-1")

    def test_unmatched_host_stays_unattributed(self):
        from unittest.mock import patch

        svc = CrowdSecService()
        d = svc._normalize_decision(_live_shape(), {"svc-1": {"other.example.com"}})
        with patch.object(
            CrowdSecService, "_get_service_hosts",
            return_value={"svc-1": {"other.example.com"}},
        ):
            enriched = svc._enrich_with_service([d])
        self.assertIsNone(enriched[0].service)
        # ...but the record itself is still fully populated.
        self.assertEqual(enriched[0].value, "34.39.228.253")


class TestNormalizeAlert(TestCase):
    def test_alert_meta_list_and_source_dict(self):
        svc = CrowdSecService()
        raw = dict(_live_shape())
        a = svc._normalize_alert(raw, {"svc-1": {"postgres-a6e2bf4a-46a67f.grid.smsly.cloud"}})
        self.assertEqual(a.source, "34.39.228.253")
        self.assertEqual(a.service, "svc-1")
        self.assertTrue(a.events)
        self.assertEqual(a.events[0]["path"], "/.git/config")
        self.assertEqual(a.events[0]["method"], "GET")
