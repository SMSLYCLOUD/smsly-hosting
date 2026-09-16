"""Unit tests for traffic-split API helpers (pure fns, no DB)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.deployments.views.service.canary import (
    aggregate_canary_counts,
    attach_latency,
    build_traffic_split_status,
    canary_verdict,
    green_state,
    parse_duration_ms,
    percentile,
    split_weights,
)


def _svc(**kwargs):
    base = dict(
        id="svc-1", name="myapp", internal_port=8000,
        deploy_strategy="CANARY", canary_percentage=25,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class SplitWeightsTests(SimpleTestCase):
    def test_active_split(self):
        self.assertEqual(split_weights("CANARY", 25), (75, 25))

    def test_inactive(self):
        self.assertEqual(split_weights("ROLLING", 25), (100, 0))
        self.assertEqual(split_weights("CANARY", 0), (100, 0))
        self.assertEqual(split_weights("CANARY", "bogus"), (100, 0))


class GreenStateTests(SimpleTestCase):
    def test_no_deployment_row(self):
        mgr = MagicMock()
        mgr.filter.return_value.exclude.return_value.exclude.return_value.order_by.return_value.first.return_value = None
        with patch("apps.deployments.models.Deployment.objects", mgr):
            state = green_state(_svc())
        self.assertEqual(state, {"present": False, "healthy": None, "deployment_id": None, "commit_hash": None})

    def test_healthy_green(self):
        row = SimpleNamespace(id="dep-1", green_container_id="abc", commit_hash="deadbee")
        mgr = MagicMock()
        mgr.filter.return_value.exclude.return_value.exclude.return_value.order_by.return_value.first.return_value = row
        container = MagicMock()
        container.attrs = {"State": {"Status": "running", "Health": {"Status": "healthy"}}}
        with patch("apps.deployments.models.Deployment.objects", mgr), \
             patch("docker.from_env") as mock_env:
            mock_env.return_value.containers.get.return_value = container
            state = green_state(_svc())
        self.assertTrue(state["present"])
        self.assertTrue(state["healthy"])
        self.assertEqual(state["deployment_id"], "dep-1")


class StatusBuilderTests(SimpleTestCase):
    def test_status_payload(self):
        payload = build_traffic_split_status(
            _svc(), green={"present": True, "healthy": True, "deployment_id": "d", "commit_hash": "c"},
        )
        self.assertEqual(payload["live_weight"], 75)
        self.assertEqual(payload["staging_weight"], 25)
        self.assertTrue(payload["split_configured"])
        # No canary file on disk in unit tests → not effectively active.
        self.assertFalse(payload["split_active"])
        self.assertFalse(payload["file_present"])

    def test_inactive_split(self):
        payload = build_traffic_split_status(
            _svc(deploy_strategy="ROLLING", canary_percentage=0),
            green={"present": False, "healthy": None, "deployment_id": None, "commit_hash": None},
        )
        self.assertFalse(payload["split_configured"])
        self.assertFalse(payload["split_active"])


class AggregateTests(SimpleTestCase):
    _VECTOR = [
        {"stream": {"upstream": "myapp:8000", "status": "200"}, "value": ["1", "750"]},
        {"stream": {"upstream": "myapp:8000", "status": "500"}, "value": ["1", "50"]},
        {"stream": {"upstream": "myapp-green-a1b:8000", "status": "200"}, "value": ["1", "190"]},
        {"stream": {"upstream": "myapp-green-a1b:8000", "status": "502"}, "value": ["1", "10"]},
        {"stream": {"status": "200"}, "value": ["1", "5"]},
    ]

    def test_aggregation(self):
        out = aggregate_canary_counts(
            self._VECTOR, 100,
            {"myapp:8000": "live", "myapp-green-a1b:8000": "staging"},
        )
        self.assertEqual(out["live"]["count"], 800)
        self.assertEqual(out["live"]["err_rate"], 6.25)
        self.assertEqual(out["live"]["rps"], 8.0)
        self.assertEqual(out["staging"]["count"], 200)
        self.assertEqual(out["staging"]["err_rate"], 5.0)
        self.assertEqual(out["unattributed"]["count"], 5)

    def test_service_addr_label_preferred(self):
        vector = [
            {"stream": {"service_addr": "172.18.0.5:8000", "status": "200"}, "value": ["1", "90"]},
            {"stream": {"service_addr": "172.18.0.6:8000", "status": "500"}, "value": ["1", "10"]},
        ]
        out = aggregate_canary_counts(
            vector, 100,
            {"172.18.0.5:8000": "live", "172.18.0.6:8000": "staging"},
        )
        self.assertEqual(out["live"]["count"], 90)
        self.assertEqual(out["staging"]["count"], 10)
        self.assertEqual(out["staging"]["err_rate"], 100.0)
        # Latency keys default to None until sampling attaches them.
        self.assertIsNone(out["live"]["p50_ms"])
        self.assertIsNone(out["staging"]["p95_ms"])

    def test_verdicts(self):
        verdict, _ = canary_verdict({
            "live": {"count": 800, "err_rate": 1.0},
            "staging": {"count": 5, "err_rate": 50.0},
        })
        self.assertEqual(verdict, "NEUTRAL")  # insufficient samples
        verdict, _ = canary_verdict({
            "live": {"count": 800, "err_rate": 1.0},
            "staging": {"count": 200, "err_rate": 12.0},
        })
        self.assertEqual(verdict, "BLOCK")
        verdict, _ = canary_verdict({
            "live": {"count": 800, "err_rate": 5.0},
            "staging": {"count": 200, "err_rate": 7.5},
        })
        self.assertEqual(verdict, "WARN")
        verdict, _ = canary_verdict({
            "live": {"count": 800, "err_rate": 1.0},
            "staging": {"count": 200, "err_rate": 1.5},
        })
        self.assertEqual(verdict, "NEUTRAL")


class LatencyHelperTests(SimpleTestCase):
    def test_percentile_nearest_rank(self):
        self.assertIsNone(percentile([], 50))
        self.assertEqual(percentile([10.0, 20.0, 30.0, 40.0], 50), 20.0)
        self.assertEqual(percentile([10.0, 20.0, 30.0, 40.0], 95), 40.0)
        self.assertEqual(percentile([5.0], 95), 5.0)

    def test_parse_duration_ms_nanoseconds(self):
        self.assertEqual(parse_duration_ms('{"Duration": 150000000}'), 150.0)
        self.assertEqual(parse_duration_ms('{"Duration": "2500000"}'), 2.5)
        self.assertIsNone(parse_duration_ms('not json'))
        self.assertIsNone(parse_duration_ms('{"RequestHost": "x.example.com"}'))
        self.assertIsNone(parse_duration_ms('{"Duration": "bogus"}'))

    def test_attach_latency(self):
        variants = aggregate_canary_counts([], 60, {})
        out = attach_latency(variants, {
            "live": [10.0, 20.0, 30.0, 40.0],
            "staging": [100.0],
            "unattributed": [],
        })
        self.assertEqual(out["live"]["p50_ms"], 20.0)
        self.assertEqual(out["live"]["p95_ms"], 40.0)
        self.assertEqual(out["staging"]["p50_ms"], 100.0)
        self.assertIsNone(out["unattributed"]["p50_ms"])
