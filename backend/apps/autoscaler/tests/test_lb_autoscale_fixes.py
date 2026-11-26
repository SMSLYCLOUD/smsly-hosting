"""Regression tests for autoscaler deadlock, metrics, scorer, reconciler, VPA, sleeper, and Celery fixes."""
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.autoscaler.engine.decision import DecisionEngine
from apps.autoscaler.engine.metrics import MetricsCollector, MetricsSnapshot
from apps.autoscaler.engine.pipeline import analyze_and_apply
from apps.autoscaler.engine.reconciler import Reconciler, _SPAWN_LOCKS
from apps.autoscaler.models.replica import ServiceReplica
from apps.autoscaler.services.tasks_autoscale import (
    build_vpa_resource_spec,
    cleanup_stuck_spawning,
)
from apps.autoscaler.sleeper import _sleep_sablier_services
from apps.deployments.models import ManagedServer, Project, Service
from apps.deployments.services.node_scorer import NodeScorer

User = get_user_model()


class DecisionEngineFixTests(SimpleTestCase):
    def _snap(self, cpu=10.0, **extra):
        return MetricsSnapshot(cpu_percent=cpu, source='db', **extra)

    def test_scale_down_not_deadlocked_at_max_replicas(self):
        engine = DecisionEngine(
            self._snap(cpu=5.0),
            running_replicas=5,
            max_replicas=5,
            min_replicas=1,
            cpu_target=80,
            last_scale_at=timezone.now() - timedelta(minutes=20),
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_down')
        self.assertGreaterEqual(rec.scale_down_by, 1)

    def test_scale_up_still_blocked_at_max_replicas(self):
        engine = DecisionEngine(
            self._snap(cpu=95.0),
            running_replicas=5,
            max_replicas=5,
            min_replicas=1,
            cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'none')
        self.assertTrue(rec.at_capacity)

    def test_scale_up_cooldown_does_not_block_scale_down_when_down_cooldown_elapsed(self):
        # Simulate cooldown_up_min=30, cooldown_down_min=5, last scaled 10m ago
        engine = DecisionEngine(
            self._snap(cpu=5.0),
            running_replicas=3,
            max_replicas=5,
            min_replicas=1,
            cpu_target=80,
            last_scale_at=timezone.now() - timedelta(minutes=10),
            cooldown_up_min=30,
            cooldown_down_min=5,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_down')

    def test_per_instance_target_honors_service_cpu_target(self):
        # cpu_target=80, 1 total instance -> 160% CPU needs int(160/80)-1+1 = 2 -> headroom clamps
        engine = DecisionEngine(
            self._snap(cpu=160.0),
            running_replicas=0,
            max_replicas=8,
            min_replicas=0,
            cpu_target=80,
        )
        rec = engine.decide()
        self.assertEqual(rec.action, 'scale_up')
        self.assertIn('≤80%', rec.reason)


class MetricsAndNodeScorerFixTests(SimpleTestCase):
    def test_prometheus_query_uses_gauge_avg_over_time_not_rate(self):
        svc = SimpleNamespace(name='api', compose_main_service='', cpu_cores=2.0)
        collector = MetricsCollector(svc, prefer='prometheus')
        queries = []

        def fake_promql(q):
            queries.append(q)
            return 42.0

        with patch.object(collector, '_promql', side_effect=fake_promql), \
                patch.object(collector, '_loki_errors', return_value={}):
            snap = collector._from_prometheus()

        self.assertEqual(snap.cpu_percent, 42.0)
        self.assertIn('avg_over_time(docker_container_cpu_usage_seconds_total', queries[0])
        self.assertNotIn('rate(docker_container_cpu_usage_seconds_total', queries[0])
        self.assertIn('/ 2.0 * 100', queries[0])

    def test_node_scorer_prefers_more_free_mem_and_disk(self):
        scorer = NodeScorer()
        healthy_node = SimpleNamespace(name='free-node', wg_address='10.100.0.2', private_ip='', host='1.1.1.2')
        full_node = SimpleNamespace(name='full-node', wg_address='10.100.0.3', private_ip='', host='1.1.1.3')

        def fake_promql(query):
            if '10.100.0.2' in query:
                if 'MemAvailable' in query:
                    return 80.0
                if 'node_cpu_seconds_total' in query:
                    return 80.0
                if 'node_filesystem_avail_bytes' in query:
                    return 80.0
            if '10.100.0.3' in query:
                if 'MemAvailable' in query:
                    return 10.0
                if 'node_cpu_seconds_total' in query:
                    return 80.0
                if 'node_filesystem_avail_bytes' in query:
                    return 10.0
            return None

        with patch.object(scorer, '_promql_avg', side_effect=fake_promql):
            ranked = scorer.score([full_node, healthy_node])

        self.assertEqual(ranked[0][0].name, 'free-node')
        self.assertGreater(ranked[0][1], ranked[1][1])
        self.assertIsNone(scorer._query_node_mem(SimpleNamespace(name='noip', wg_address='', private_ip='', host='')))


class VpaAndCeleryFixTests(SimpleTestCase):
    def test_vpa_spec_exact_cpu_and_consistent_memory_ceiling(self):
        svc = SimpleNamespace(memory_mb=512, cpu_cores=2.0)
        spec = build_vpa_resource_spec(svc, ceiling=1.5)
        self.assertEqual(spec['docker_kwargs']['cpu_shares'], 2048)
        self.assertEqual(spec['docker_kwargs']['cpu_quota'], 300000)
        self.assertEqual(spec['docker_kwargs']['mem_reservation'], '512m')
        self.assertEqual(spec['docker_kwargs']['mem_limit'], '768m')
        self.assertIn('--memory=768m', spec['update_parts'])
        self.assertIn('--cpu-shares=2048', spec['update_parts'])

    def test_celery_autoscaler_routes_off_deploy_queue_and_single_beat(self):
        from config import celery as celery_cfg

        self.assertEqual(
            celery_cfg.app.conf.task_routes['apps.autoscaler.services.tasks_autoscale.analyze_all_services_task']['queue'],
            'fast',
        )
        self.assertEqual(
            celery_cfg.app.conf.task_routes['apps.core.tasks.metrics.collect_metrics_task']['queue'],
            'fast',
        )
        self.assertNotIn('check-autoscale-every-30s', celery_cfg.app.conf.beat_schedule)
        self.assertIn('auto-scaling-analyze-every-3m', celery_cfg.app.conf.beat_schedule)


class ReconcilerAndLifecycleFixTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='fix-user', password='x')
        self.project = Project.objects.create(name='fix-proj', owner=self.user)
        self.remote_node = ManagedServer.objects.create(
            owner=self.user,
            name='worker-1',
            host='10.0.0.10',
            wg_address='10.100.0.2',
            is_primary=False,
            status=ManagedServer.Status.ONLINE,
            allow_user_workloads=True,
            node_number=1,
        )
        self.service = Service.objects.create(
            name='remote-app',
            owner=self.user,
            project=self.project,
            server=self.remote_node,
            status='ACTIVE',
            min_replicas=1,
            max_replicas=6,
            autoscale_cpu_target=80,
        )

    def test_remote_primary_spawns_on_assigned_server_and_multi_overflow(self):
        _SPAWN_LOCKS.pop(str(self.service.id), None)
        spawned_targets = []

        def fake_spawn(svc, node, replica):
            spawned_targets.append(node.name)
            replica.container_name = f"{svc.name}-replica-{replica.id.hex[:8]}"
            replica.status = 'RUNNING'
            replica.save(update_fields=['container_name', 'status'])
            return replica

        with patch('apps.deployments.services.spawning_service.SpawningService.spawn_local') as mock_local, \
                patch('apps.deployments.services.spawning_service.SpawningService.spawn', side_effect=fake_spawn):
            rec = Reconciler(self.service).apply(
                DecisionEngine(
                    MetricsSnapshot(cpu_percent=350.0, source='db'),
                    running_replicas=0, max_replicas=6, cpu_target=80,
                ).decide()
            )
        mock_local.assert_not_called()
        self.assertTrue(rec.applied)
        self.assertGreaterEqual(rec.spawned, 2)
        self.assertTrue(all(n == 'worker-1' for n in spawned_targets))

    def test_cleanup_stuck_spawning_protects_newly_draining_old_replica(self):
        rep = ServiceReplica.objects.create(
            service=self.service,
            node=None,
            container_name='remote-app-replica-old1',
            status='DRAINING',
        )
        ServiceReplica.objects.filter(id=rep.id).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        with patch('apps.deployments.services.spawning_service.SpawningService.destroy') as mock_destroy:
            # First sweep records DRAINING entry time — must NOT kill yet.
            cleanup_stuck_spawning()
            mock_destroy.assert_not_called()
            # Simulate entry timestamp older than threshold -> now sweep destroys.
            cache.set(f"autoscale:transitional:{rep.id}:DRAINING", time.time() - 400, timeout=3600)
            cleanup_stuck_spawning()
            mock_destroy.assert_called_once()

    def test_idle_none_evaluation_does_not_block_subsequent_spike_within_window(self):
        local_svc = Service.objects.create(
            name='local-dedup', owner=self.user, project=self.project,
            status='ACTIVE', min_replicas=0, max_replicas=4,
        )
        with patch('apps.autoscaler.engine.metrics.MetricsCollector.collect') as mock_collect, \
                patch('apps.deployments.services.spawning_service.SpawningService.spawn_local') as mock_spawn:
            mock_collect.return_value = MetricsSnapshot(cpu_percent=35.0, source='db')
            res1 = analyze_and_apply(local_svc, min_interval_seconds=120)
            self.assertFalse(res1.applied)
            mock_collect.return_value = MetricsSnapshot(cpu_percent=95.0, source='db')
            res2 = analyze_and_apply(local_svc, min_interval_seconds=120)
            self.assertTrue(res2.applied)
            mock_spawn.assert_called()

    def test_sleeper_co_sleeps_enrolled_local_replicas(self):
        sab_svc = Service.objects.create(
            name='sab-app', owner=self.user, project=self.project,
            status='ACTIVE', sablier_enabled=True, sablier_session='5m',
        )
        ServiceReplica.objects.create(
            service=sab_svc, node=None,
            container_name='sab-app-replica-1', status='RUNNING',
        )
        primary_c = MagicMock(status='running')
        primary_c.attrs = {'Config': {'Labels': {'sablier.enable': 'true', 'sablier.group': 'sab-app'}}}
        rep_c = MagicMock(status='running')
        rep_c.attrs = {'Config': {'Labels': {'sablier.enable': 'true', 'sablier.group': 'sab-app'}}}

        client = MagicMock()
        client.containers.get.side_effect = lambda cname: {
            'sab-app': primary_c,
            'sab-app-replica-1': rep_c,
        }[cname]

        res = _sleep_sablier_services(client)
        self.assertIn('sab-app', res['slept'])
        primary_c.stop.assert_called_once()
        rep_c.stop.assert_called_once()
