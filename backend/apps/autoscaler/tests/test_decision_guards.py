"""Guard tests for the unified DecisionEngine outage semantics.

Regression coverage for the autoscaler review fixes:
  * unknown metrics (cpu_percent=None) must HOLD, never scale down;
  * idle metrics (cpu_percent=0.0) must still scale down;
  * OOM/crash must defer while a spawn is already in flight.
"""
from django.test import SimpleTestCase
from django.utils import timezone

from apps.autoscaler.engine.decision import DecisionEngine
from apps.autoscaler.engine.metrics import MetricsSnapshot


def _engine(metrics, **overrides):
    kwargs = {
        'running_replicas': 2,
        'max_replicas': 5,
        'min_replicas': 1,
        'cpu_target': 50,
        'last_scale_at': None,
        'spawning_in_progress': False,
        'now': timezone.now(),
    }
    kwargs.update(overrides)
    return DecisionEngine(metrics, **kwargs)


class DecisionOutageGuardsTests(SimpleTestCase):
    def test_none_cpu_holds_steady(self):
        snap = MetricsSnapshot(cpu_percent=None, source='none')
        rec = _engine(snap).decide()
        self.assertEqual(rec.action, 'none')
        self.assertIn('holding steady', rec.reason.lower())

    def test_zero_cpu_still_scales_down(self):
        snap = MetricsSnapshot(cpu_percent=0.0, source='db')
        rec = _engine(snap).decide()
        self.assertEqual(rec.action, 'scale_down')
        self.assertEqual(rec.scale_down_by, 1)

    def test_oom_defers_while_spawning(self):
        snap = MetricsSnapshot(cpu_percent=90.0, oom_detected=True, source='prometheus')
        rec = _engine(snap, spawning_in_progress=True).decide()
        self.assertEqual(rec.action, 'none')
        self.assertIn('spawn in progress', rec.reason.lower())

    def test_oom_fires_without_spawning(self):
        snap = MetricsSnapshot(cpu_percent=90.0, oom_detected=True, source='prometheus')
        rec = _engine(snap).decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.urgency, 'critical')
