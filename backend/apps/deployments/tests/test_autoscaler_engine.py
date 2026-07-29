# pylint: disable=invalid-name
"""
Tests for the unified autoscaler engine.

Covers:
  * DecisionEngine output shape and policy merging
  * MetricsCollector fallback chain (DB → Prometheus → Docker)
  * Reconciler per-service lock (concurrent spawn race condition)
  * analyze_and_apply end-to-end with mocked spawner
"""
import threading
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.autoscaler.engine.decision import (
    DEFAULT_CPU_LOW,
    DecisionEngine,
    Recommendation,
)
from apps.autoscaler.engine.metrics import (
    MetricsCollector,
    MetricsSnapshot,
)
from apps.autoscaler.engine.reconciler import Reconciler, ScaleResult

User = get_user_model()


# ── Decision engine tests ──────────────────────────────────────────────────

class DecisionEngineBasicTests(TestCase):
    """Pure-function tests for the decision engine. No I/O."""

    def _metrics(self, **overrides):
        defaults = {
            'cpu_percent': 50.0,
            'memory_mb': 100.0,
            'memory_trend_mb_per_min': None,
            'error_count_1h': 0,
            'oom_detected': False,
            'crash_loop': False,
            'has_errors': False,
            'source': 'test',
        }
        defaults.update(overrides)
        return MetricsSnapshot(**defaults)

    def test_no_metrics_returns_none(self):
        engine = DecisionEngine(self._metrics(cpu_percent=None), running_replicas=0,
                                max_replicas=3, cpu_target=80)
        rec = engine.decide()
        self.assertEqual(rec.action, 'none')

    def test_high_cpu_triggers_scale_up(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=90.0),
            running_replicas=1, max_replicas=5, cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertGreaterEqual(rec.scale_up_by, 1)
        self.assertIn(rec.urgency, ('high', 'critical', 'medium'))

    def test_oom_triggers_critical_scale_up(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=20.0, oom_detected=True),
            running_replicas=0, max_replicas=5, cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.urgency, 'critical')

    def test_crash_loop_triggers_critical_scale_up(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=20.0, crash_loop=True),
            running_replicas=0, max_replicas=5, cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertEqual(rec.urgency, 'critical')

    def test_at_capacity_blocks_scale_up(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=95.0),
            running_replicas=5, max_replicas=5, cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'none')
        self.assertTrue(rec.at_capacity)

    def test_low_cpu_triggers_scale_down(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=DEFAULT_CPU_LOW - 5.0),
            running_replicas=2, max_replicas=5, cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_down')

    def test_low_cpu_with_no_replicas_does_not_scale_down(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=5.0),
            running_replicas=0, max_replicas=5, cpu_target=80,
        )
        rec = engine.decide()
        self.assertNotEqual(rec.action, 'scale_down')

    def test_cooldown_blocks_scale_up(self):
        last = timezone.now() - timedelta(seconds=30)
        engine = DecisionEngine(
            self._metrics(cpu_percent=95.0),
            running_replicas=1, max_replicas=5, cpu_target=80,
            last_scale_at=last, cooldown_up_min=1,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'none')
        self.assertTrue(rec.cooldown_active)

    def test_spawning_in_progress_defers(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=95.0),
            running_replicas=1, max_replicas=5, cpu_target=80,
            spawning_in_progress=True,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'none')
        self.assertTrue(rec.spawning_in_progress)

    def test_recommendation_to_dict_shape(self):
        rec = Recommendation(action='scale_up', reason='test', scale_up_by=2, urgency='high')
        d = rec.to_dict()
        self.assertEqual(d['action'], 'scale_up')
        self.assertEqual(d['scale_up_by'], 2)
        self.assertEqual(d['urgency'], 'high')

    def test_oom_at_capacity_does_not_scale_up(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=20.0, oom_detected=True),
            running_replicas=5, max_replicas=5, cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'none')

    def test_memory_trend_triggers_scale_up(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=10.0, memory_trend_mb_per_min=50.0),
            running_replicas=1, max_replicas=5, cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertIn('Memory growing', rec.reason)

    def test_scale_up_never_exceeds_max_replicas(self):
        engine = DecisionEngine(
            self._metrics(cpu_percent=200.0),  # absurdly high
            running_replicas=1, max_replicas=3, cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_up')
        # (1 running + scale_up_by) must not exceed max_replicas
        self.assertLessEqual(1 + rec.scale_up_by, 3)


# ── Metrics collector tests ────────────────────────────────────────────────

class MetricsCollectorTests(TestCase):

    def test_returns_snapshot_with_source(self):
        from apps.deployments.models import Project
        from apps.deployments.models.core import Service

        user = User.objects.create_user(username='mc-user', password='x')
        project = Project.objects.create(name='mc-proj', owner=user)
        service = Service.objects.create(
            name='mc-svc', owner=user, project=project,
        )
        collector = MetricsCollector(service)
        snap = collector.collect()
        # In a hermetic test env, prometheus is unreachable, docker socket
        # doesn't exist, DB has no metrics. The collector should return
        # a snapshot with source='db' (the first tried) and no values.
        self.assertIsInstance(snap, MetricsSnapshot)
        self.assertIn(snap.source, ('db', 'prometheus', 'docker', 'none'))
        self.assertIsNone(snap.cpu_percent)

    def test_prefer_prometheus_uses_prometheus_first(self):
        from apps.deployments.models import Project
        from apps.deployments.models.core import Service

        user = User.objects.create_user(username='mc-pp', password='x')
        project = Project.objects.create(name='mc-proj-pp', owner=user)
        service = Service.objects.create(
            name='mc-pp-svc', owner=user, project=project,
        )
        collector = MetricsCollector(service, prefer='prometheus')
        with patch.object(MetricsCollector, '_from_prometheus') as mock_p:
            mock_p.return_value = MetricsSnapshot(cpu_percent=55.0, source='prometheus')
            snap = collector.collect()
            mock_p.assert_called_once()
            self.assertEqual(snap.source, 'prometheus')

    def test_prefer_db_uses_db_first(self):
        from apps.deployments.models import Project
        from apps.deployments.models.core import Service
        from apps.deployments.models.metrics import ServiceMetric

        user = User.objects.create_user(username='mc-db', password='x')
        project = Project.objects.create(name='mc-proj-db', owner=user)
        service = Service.objects.create(
            name='mc-db-svc', owner=user, project=project,
        )
        ServiceMetric.objects.create(
            service=service,
            cpu_usage=1, cpu_limit=2,
            memory_usage=512, memory_limit=1024,
            timestamp=timezone.now(),
        )
        collector = MetricsCollector(service, prefer='db')
        snap = collector.collect()
        self.assertEqual(snap.source, 'db')
        self.assertIsNotNone(snap.cpu_percent)
        # 1/2 cores * 100 = 50%
        self.assertAlmostEqual(snap.cpu_percent, 50.0, places=1)


# ── Reconciler race condition test ─────────────────────────────────────────

class ReconcilerRaceConditionTests(TestCase):
    """The two Celery beat tasks must not double-spawn replicas when
    their intervals overlap. The Reconciler holds a per-service lock
    that serializes the work."""

    def setUp(self):
        from apps.deployments.models import Project
        from apps.deployments.models.core import Service

        self.user = User.objects.create_user(username='race-user', password='x')
        self.project = Project.objects.create(name='race-proj', owner=self.user)
        self.service = Service.objects.create(
            name='race-svc', owner=self.user, project=self.project,
            min_replicas=1, max_replicas=5, autoscale_cpu_target=80,
        )

    def test_concurrent_reconcile_serializes_per_service(self):
        """Five threads calling Reconciler.apply() for the same service
        with the same scale_up recommendation must NOT all run
        concurrently — the per-service lock must serialize them.

        SQLite (the test backend) can't handle concurrent writes, so
        the work done inside the lock is mocked to a no-op that
        records the call. The assertion is that the calls happen
        sequentially (never overlapping), and that exactly one call
        from the batch ends up entering the critical section at a time.
        """
        from apps.autoscaler.engine.reconciler import _SPAWN_LOCKS

        _SPAWN_LOCKS.pop(str(self.service.id), None)

        rec = Recommendation(action='scale_up', reason='test', scale_up_by=3)

        enter_count = 0
        in_section = 0
        max_concurrent = 0
        counter_lock = threading.Lock()
        events: list = []

        def slow_reconcile(recommendation):
            nonlocal enter_count, in_section, max_concurrent
            with counter_lock:
                enter_count += 1
                in_section += 1
                max_concurrent = max(max_concurrent, in_section)
            # Hold the lock for a moment to force the race window
            threading.Event().wait(0.05)
            events.append(threading.get_ident())
            with counter_lock:
                in_section -= 1
            return ScaleResult(recommendation, applied=True, spawned=1)

        # We patch the engine's _scale_up to record concurrency rather
        # than actually spawn, so the per-service lock semantics are
        # the only thing under test.
        results: list = []
        errors: list = []

        def worker():
            try:
                r = Reconciler(self.service).apply(rec)
                results.append(r)
            except Exception as exc:
                errors.append(exc)

        with patch.object(Reconciler, '_scale_up', side_effect=slow_reconcile):
            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
                self.assertFalse(t.is_alive(), "Worker thread hung — likely a lock leak")

        self.assertEqual(errors, [])
        # All 5 calls should have been processed (no deadlock)
        self.assertEqual(enter_count, 5)
        # The per-service lock must have serialized them: never
        # more than 1 in the critical section at any time.
        self.assertEqual(
            max_concurrent, 1,
            f"Per-service lock failed: max concurrent = {max_concurrent}"
        )
        # And all 5 reports a successful apply (the lock is about
        # serialization, not deduplication — the per-service cooldown
        # in the engine handles the latter).
        self.assertEqual(sum(1 for r in results if r.applied), 5)

    def test_lock_is_per_service(self):
        """The lock for service A must not block work for service B."""
        from apps.autoscaler.engine.reconciler import _SPAWN_LOCKS
        from apps.deployments.models.core import Service

        other_service = Service.objects.create(
            name='race-svc-b', owner=self.user, project=self.project,
            min_replicas=1, max_replicas=5, autoscale_cpu_target=80,
        )
        _SPAWN_LOCKS.pop(str(self.service.id), None)
        _SPAWN_LOCKS.pop(str(other_service.id), None)

        Recommendation(action='scale_up', reason='test', scale_up_by=1)

        # Hold the lock for service A
        lock_a = _SPAWN_LOCKS.get(str(self.service.id)) or Reconciler(self.service)._lock
        lock_b = _SPAWN_LOCKS.get(str(other_service.id)) or Reconciler(other_service)._lock
        self.assertIsNot(lock_a, lock_b, "Each service must have its own lock")


# ── end-to-end pipeline test ───────────────────────────────────────────────

class AnalyzeAndApplyTests(TestCase):
    """End-to-end test of the pipeline with a mocked spawner."""

    def setUp(self):
        from apps.deployments.models import Project
        from apps.deployments.models.core import Service
        from apps.deployments.models.metrics import ServiceMetric

        self.user = User.objects.create_user(username='pipe-user', password='x')
        self.project = Project.objects.create(name='pipe-proj', owner=self.user)
        self.service = Service.objects.create(
            name='pipe-svc', owner=self.user, project=self.project,
            min_replicas=1, max_replicas=5, autoscale_cpu_target=80,
        )
        # Seed DB metrics so the analyzer finds data without Prometheus
        ServiceMetric.objects.create(
            service=self.service,
            cpu_usage=1.6, cpu_limit=2,  # 80% CPU
            memory_usage=512, memory_limit=1024,
            timestamp=timezone.now(),
        )

    def test_high_cpu_with_db_metrics_scales_up(self):
        from apps.autoscaler.engine.reconciler import _SPAWN_LOCKS
        _SPAWN_LOCKS.pop(str(self.service.id), None)

        with patch('apps.deployments.services.spawning_service.SpawningService.spawn_local') as mock_spawn, \
             patch('apps.deployments.services.spawning_service.SpawningService._check_local_capacity'):
            mock_spawn.return_value = MagicMock()
            from apps.autoscaler.engine.pipeline import analyze_and_apply
            result = analyze_and_apply(self.service)
            self.assertTrue(result.applied)
            self.assertEqual(result.recommendation.action, 'scale_up')
            self.assertGreaterEqual(result.spawned, 1)

    def test_no_metrics_returns_no_action(self):
        from apps.autoscaler.engine.reconciler import _SPAWN_LOCKS
        _SPAWN_LOCKS.pop(str(self.service.id), None)
        # Clear the metrics so the analyzer has no data
        from apps.deployments.models.metrics import ServiceMetric
        ServiceMetric.objects.filter(service=self.service).delete()

        with patch('apps.deployments.services.spawning_service.SpawningService.spawn_local'), \
             patch('apps.deployments.services.spawning_service.SpawningService._check_local_capacity'):
            from apps.autoscaler.engine.pipeline import analyze_and_apply
            result = analyze_and_apply(self.service)
            self.assertFalse(result.applied)
            self.assertEqual(result.recommendation.action, 'none')
