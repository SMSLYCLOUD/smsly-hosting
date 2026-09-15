"""Step-size tests for the autoscaler elongation update.

Demanding services must jump by 2 (high) or 4 (critical/OOM) replicas
per tick; deep-idle services contract 2 at a time down toward
min_replicas (0 allowed — scale-to-zero parks replicas, the primary
keeps serving). The formula still wins when it demands even more.
"""
from django.test import SimpleTestCase
from django.utils import timezone

from apps.autoscaler.engine.decision import DecisionEngine
from apps.autoscaler.engine.metrics import MetricsSnapshot


def _engine(metrics, **overrides):
    kwargs = {
        'running_replicas': 1,
        'max_replicas': 8,
        'min_replicas': 0,
        'cpu_target': 50,
        'last_scale_at': None,
        'spawning_in_progress': False,
        'now': timezone.now(),
    }
    kwargs.update(overrides)
    return DecisionEngine(metrics, **kwargs)


class ScaleStepTests(SimpleTestCase):
    def test_critical_cpu_scales_by_four(self):
        snap = MetricsSnapshot(cpu_percent=95.0, source='prometheus')
        rec = _engine(snap).decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.urgency, 'critical')
        self.assertEqual(rec.scale_up_by, 4)

    def test_high_cpu_scales_by_two(self):
        # cpu 85: formula alone yields 1 (int(85/50)=1, total=2 -> 0 -> floor 1)
        snap = MetricsSnapshot(cpu_percent=85.0, source='prometheus')
        rec = _engine(snap).decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.urgency, 'high')
        self.assertEqual(rec.scale_up_by, 2)

    def test_medium_cpu_still_scales_by_one(self):
        snap = MetricsSnapshot(cpu_percent=60.0, source='prometheus')
        rec = _engine(snap).decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.urgency, 'medium')
        self.assertEqual(rec.scale_up_by, 1)

    def test_formula_wins_when_larger(self):
        # cpu 400: formula needs 7 — more than the critical step of 4.
        snap = MetricsSnapshot(cpu_percent=400.0, source='prometheus')
        rec = _engine(snap, max_replicas=10).decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.scale_up_by, 7)

    def test_steps_clamp_to_headroom(self):
        snap = MetricsSnapshot(cpu_percent=95.0, source='prometheus')
        rec = _engine(snap, running_replicas=7, max_replicas=8).decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.scale_up_by, 1)

    def test_oom_scales_by_four(self):
        snap = MetricsSnapshot(cpu_percent=90.0, oom_detected=True, source='prometheus')
        rec = _engine(snap).decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.urgency, 'critical')
        self.assertEqual(rec.scale_up_by, 4)

    def test_oom_clamps_to_headroom(self):
        snap = MetricsSnapshot(cpu_percent=90.0, oom_detected=True, source='prometheus')
        rec = _engine(snap, running_replicas=7, max_replicas=8).decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.scale_up_by, 1)

    def test_deep_idle_contracts_by_two(self):
        snap = MetricsSnapshot(cpu_percent=5.0, source='db')
        rec = _engine(snap, running_replicas=4, min_replicas=0).decide()
        self.assertEqual(rec.action, 'scale_down')
        self.assertEqual(rec.scale_down_by, 2)

    def test_shallow_idle_contracts_by_one(self):
        snap = MetricsSnapshot(cpu_percent=20.0, source='db')
        rec = _engine(snap, running_replicas=4, min_replicas=0).decide()
        self.assertEqual(rec.action, 'scale_down')
        self.assertEqual(rec.scale_down_by, 1)

    def test_scale_to_zero_reaches_min_zero(self):
        # Engine asks for 2; the reconciler clamps to the 1 removable
        # replica — the point is min_replicas=0 is reachable, not skipped.
        snap = MetricsSnapshot(cpu_percent=0.0, source='db')
        rec = _engine(snap, running_replicas=1, min_replicas=0).decide()
        self.assertEqual(rec.action, 'scale_down')
        self.assertEqual(rec.scale_down_by, 1)

    def test_none_cpu_still_holds(self):
        snap = MetricsSnapshot(cpu_percent=None, source='none')
        rec = _engine(snap, running_replicas=4, min_replicas=0).decide()
        self.assertEqual(rec.action, 'none')
