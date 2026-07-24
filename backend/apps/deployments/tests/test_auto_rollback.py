from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.models.audit import AuditLog
from apps.deployments.services.auto_rollback import (
    AUTO_ROLLBACK_THRESHOLD,
    AutoRollbackEngine,
    Trigger,
    clear_rollback_heartbeat,
    get_stuck_rollback_heartbeats,
    monitor_stuck_rollback_heartbeats,
)


class AutoRollbackEngineTests(TestCase):
    """Tests for the centralized auto-rollback engine.

    Note on mocking: ``AutoRollbackEngine.trigger`` performs LOCAL
    imports inside the function body (to avoid circular imports at
    module load). Therefore the patch target must be the SOURCE module
    where the symbol is defined, not the consumer module.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='test', password='pass')
        self.provider = CloudProvider.objects.create(
            name='provider', provider_type='LOCAL', is_active=True,
        )
        self.service = Service.objects.create(
            name='test-svc',
            owner=self.user,
            provider=self.provider,
            auto_rollback_enabled=True,
            # Use a small per-service threshold so tests are fast AND
            # validate the per-service override path (rather than relying
            # on the global default).
            auto_rollback_threshold=2,
        )
        self.active_deploy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='aaaaaa111111',
            commit_message='Initial good deploy',
        )

    def tearDown(self):
        cache.clear()

    def _create_failed_deployments(self, count: int):
        for i in range(count):
            Deployment.objects.create(
                service=self.service,
                status=Deployment.Status.FAILED,
                commit_hash=f'fail{i:06x}',
                commit_message=f'Failed deploy {i}',
            )

    def _create_failing_trigger(self, trigger=Trigger.CONSECUTIVE_FAILURES):
        """Create enough failures to cross the threshold and return the
        most recent failed deployment for ``failed_deployment`` arg."""
        self._create_failed_deployments(self.service.auto_rollback_threshold)
        return self.service.deployments.filter(
            status=Deployment.Status.FAILED,
        ).order_by('-created_at').first()

    # ── opt-out ─────────────────────────────────────────────────

    def test_disabled_by_service_config(self):
        self.service.auto_rollback_enabled = False
        self.service.save()
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        self.assertFalse(result.fired)
        self.assertEqual(result.reason, 'disabled_by_service_config')

    # ── dedup lock ───────────────────────────────────────────────

    def test_dedup_lock_held(self):
        lock_key = AutoRollbackEngine._lock_key(self.service.id)
        cache.set(lock_key, '1', timeout=60)
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        self.assertFalse(result.fired)
        self.assertEqual(result.reason, 'dedup_lock_held')

    # ── threshold ────────────────────────────────────────────────

    def test_below_threshold(self):
        self._create_failed_deployments(self.service.auto_rollback_threshold - 1)
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        self.assertFalse(result.fired)
        self.assertIn('below_threshold', result.reason)

    def test_global_default_threshold_observed(self):
        """A service with no override must respect the global default."""
        self.service.auto_rollback_threshold = None
        self.service.save()
        # AUTO_ROLLBACK_THRESHOLD - 1 failures is still below global default
        self._create_failed_deployments(AUTO_ROLLBACK_THRESHOLD - 1)
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        self.assertFalse(result.fired)
        self.assertIn('below_threshold', result.reason)

    # ── no prior success ────────────────────────────────────────

    def test_no_prior_successful_deployment(self):
        self.active_deploy.delete()
        self._create_failed_deployments(self.service.auto_rollback_threshold)
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        self.assertFalse(result.fired)
        self.assertEqual(result.reason, 'no_prior_successful_deployment')

    # ── in-flight guard ─────────────────────────────────────────

    def test_rollback_in_flight(self):
        self._create_failed_deployments(self.service.auto_rollback_threshold)
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='aaaaaa111111',
            is_rollback=True,
        )
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        self.assertFalse(result.fired)
        self.assertEqual(result.reason, 'rollback_in_flight')

    # ── cooldown ────────────────────────────────────────────────

    def test_cooldown_window_active(self):
        self._create_failed_deployments(self.service.auto_rollback_threshold)
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash='aaaaaa111111',
            is_rollback=True,
        )
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        self.assertFalse(result.fired)
        self.assertIn('cooldown', result.reason)

    # ── successful trigger ──────────────────────────────────────
    # Patches target the SOURCE module — see class docstring.

    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    @patch('apps.deployments.tasks_deploy.enqueue_smart_deploy_task')
    def test_successful_trigger_creates_rollback(
        self, mock_enqueue, mock_notify,
    ):
        failed = self._create_failing_trigger()
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
            reason_detail='test trigger',
            failed_deployment=failed,
        )
        self.assertTrue(result.fired)
        self.assertIsNotNone(result.rollback_id)

        rollback = Deployment.objects.get(id=result.rollback_id)
        self.assertTrue(rollback.is_rollback)
        self.assertEqual(rollback.commit_hash, self.active_deploy.commit_hash)
        self.assertEqual(rollback.status, Deployment.Status.QUEUED)
        self.assertIn('AUTO-ROLLBACK', rollback.commit_message)

        # rollback_from points AT the failed deployment, not the target.
        self.assertEqual(rollback.rollback_from_id, failed.id)

        mock_enqueue.assert_called_once()
        # Notification was dispatched AFTER the row was created so the
        # reason text should include the rollback deployment id.
        mock_notify.delay.assert_called_once()
        _, kwargs = mock_notify.delay.call_args
        self.assertIn(str(rollback.id), kwargs['reason'])

    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    @patch('apps.deployments.tasks_deploy.enqueue_smart_deploy_task')
    def test_successful_trigger_sets_heartbeat_and_registry(
        self, mock_enqueue, mock_notify,
    ):
        self._create_failing_trigger(trigger=Trigger.HEALTH_CHECK_FALLBACK)
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.HEALTH_CHECK_FALLBACK,
        )
        self.assertTrue(result.fired)

        hb = cache.get(AutoRollbackEngine._heartbeat_key(result.rollback_id))
        self.assertIsNotNone(hb)
        self.assertEqual(hb['service_id'], str(self.service.id))
        self.assertEqual(hb['trigger'], Trigger.HEALTH_CHECK_FALLBACK)
        self.assertEqual(hb['target_commit'], self.active_deploy.commit_hash)

        # Registry was updated so the monitor can find this rollback.
        # The registry stores str(id) and result.rollback_id is also
        # a str, so they should match directly.
        registry = cache.get('rollback-heartbeat-registry') or set()
        self.assertIn(result.rollback_id, registry)

    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    @patch('apps.deployments.tasks_deploy.enqueue_smart_deploy_task')
    def test_successful_trigger_writes_audit_log(
        self, mock_enqueue, mock_notify,
    ):
        failed = self._create_failing_trigger()
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
            reason_detail='detailed reason',
            failed_deployment=failed,
        )
        self.assertTrue(result.fired)

        audit = AuditLog.objects.filter(
            actor='AUTO_ROLLBACK_ENGINE',
            action='AUTO_ROLLBACK_TRIGGERED',
        )
        self.assertEqual(audit.count(), 1)
        log = audit.first()
        self.assertEqual(log.target, f'Service:{self.service.name}')
        self.assertEqual(log.metadata['trigger'], Trigger.CONSECUTIVE_FAILURES)
        self.assertEqual(log.metadata['reason_detail'], 'detailed reason')
        self.assertEqual(log.metadata['rollback_deployment_id'], result.rollback_id)
        self.assertEqual(log.metadata['rolled_back_from_id'], str(failed.id))
        self.assertEqual(
            log.metadata['target_commit_hash'],
            self.active_deploy.commit_hash,
        )

    # ── rollback_from semantic ──────────────────────────────────

    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    @patch('apps.deployments.tasks_deploy.enqueue_smart_deploy_task')
    def test_rollback_from_is_none_when_failed_deployment_omitted(
        self, mock_enqueue, mock_notify,
    ):
        """Health monitor calls without a failed_deployment — the field
        must stay null rather than fall back to the target (which would
        semantically invert the relationship).
        """
        self._create_failing_trigger()
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.HEALTH_CHECK_FALLBACK,
        )
        self.assertTrue(result.fired)
        rollback = Deployment.objects.get(id=result.rollback_id)
        self.assertIsNone(rollback.rollback_from_id)

    # ── no active provider ──────────────────────────────────────

    @patch('apps.deployments.tasks.deploy.helpers._resolve_provider_for_service')
    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    @patch('apps.deployments.tasks_deploy.enqueue_smart_deploy_task')
    def test_no_active_provider_rollback_queued(
        self, mock_enqueue, mock_notify, mock_resolve,
    ):
        mock_resolve.return_value = None
        self._create_failing_trigger(trigger=Trigger.AI_CRASH_LOOP)
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.AI_CRASH_LOOP,
        )
        self.assertTrue(result.fired)
        self.assertEqual(result.reason, 'no_active_provider_rollback_queued')

    # ── heartbeat helpers ───────────────────────────────────────

    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    @patch('apps.deployments.tasks_deploy.enqueue_smart_deploy_task')
    def test_clear_rollback_heartbeat_removes_from_registry(
        self, mock_enqueue, mock_notify,
    ):
        self._create_failing_trigger()
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        hb_key = AutoRollbackEngine._heartbeat_key(result.rollback_id)
        self.assertIsNotNone(cache.get(hb_key))
        registry = cache.get('rollback-heartbeat-registry') or set()
        self.assertIn(result.rollback_id, registry)

        # Pass the str(id) — same type as registry storage.
        clear_rollback_heartbeat(result.rollback_id)

        self.assertIsNone(cache.get(hb_key))
        registry = cache.get('rollback-heartbeat-registry') or set()
        self.assertNotIn(result.rollback_id, registry)

    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    @patch('apps.deployments.tasks_deploy.enqueue_smart_deploy_task')
    def test_clear_rollback_heartbeat_accepts_uuid(
        self, mock_enqueue, mock_notify,
    ):
        """A caller passing ``Deployment.id`` (UUID) must also work."""
        self._create_failing_trigger()
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        # Resolve a UUID object from the database row.
        rollback = Deployment.objects.get(id=result.rollback_id)
        clear_rollback_heartbeat(rollback.id)

        self.assertIsNone(cache.get(AutoRollbackEngine._heartbeat_key(rollback.id)))
        registry = cache.get('rollback-heartbeat-registry') or set()
        self.assertNotIn(result.rollback_id, registry)

    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    @patch('apps.deployments.tasks_deploy.enqueue_smart_deploy_task')
    def test_get_stuck_rollback_heartbeats(
        self, mock_enqueue, mock_notify,
    ):
        self._create_failing_trigger()
        result = AutoRollbackEngine.trigger(
            service=self.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
        )
        # Fresh rollback — not stuck yet.
        stuck = get_stuck_rollback_heartbeats()
        self.assertEqual(len(stuck), 0)

        # Backdate the heartbeat to simulate a rollback sitting too long.
        hb_key = AutoRollbackEngine._heartbeat_key(result.rollback_id)
        cache.set(
            hb_key,
            {
                'service_id': str(self.service.id),
                'service_name': self.service.name,
                'trigger': 'test',
                'target_commit': 'abc123',
                'queued_at': (
                    timezone.now() - timezone.timedelta(hours=1)
                ).isoformat(),
            },
            timeout=300,
        )
        registry = cache.get('rollback-heartbeat-registry') or set()
        registry.add(result.rollback_id)
        cache.set('rollback-heartbeat-registry', registry, timeout=86400)

        stuck = get_stuck_rollback_heartbeats()
        self.assertEqual(len(stuck), 1)
        rid, payload = stuck[0]
        self.assertEqual(rid, result.rollback_id)

    # ── monitor task actually alerts + audits + clears ──────────

    @patch('apps.core.tasks.alerts.notify_auto_rollback')
    def test_monitor_alerts_and_audits_and_clears(self, mock_notify):
        # Plant a stuck heartbeat directly.
        hb_key = AutoRollbackEngine._heartbeat_key('fake-rollback-id')
        cache.set(
            hb_key,
            {
                'service_id': str(self.service.id),
                'service_name': self.service.name,
                'trigger': 'test_trigger',
                'target_commit': 'deadbee',
                'queued_at': (
                    timezone.now() - timezone.timedelta(hours=1)
                ).isoformat(),
            },
            timeout=300,
        )
        registry = {'fake-rollback-id'}
        cache.set('rollback-heartbeat-registry', registry, timeout=86400)

        result = monitor_stuck_rollback_heartbeats()

        self.assertEqual(result['stuck_count'], 1)
        mock_notify.delay.assert_called_once()
        _, kwargs = mock_notify.delay.call_args
        self.assertEqual(kwargs['service_id'], str(self.service.id))
        self.assertEqual(kwargs['trigger'], 'stuck_rollback_monitor')
        self.assertIn('stuck', kwargs['reason'].lower())

        # Audit log entry was written.
        self.assertTrue(
            AuditLog.objects.filter(
                actor='AUTO_ROLLBACK_MONITOR',
                action='STUCK_ROLLBACK_DETECTED',
            ).exists()
        )

        # Heartbeat + registry entry were cleared so we don't re-alert.
        self.assertIsNone(cache.get(hb_key))
        self.assertNotIn(
            'fake-rollback-id',
            cache.get('rollback-heartbeat-registry') or set(),
        )

    def test_monitor_no_op_when_no_stuck(self):
        result = monitor_stuck_rollback_heartbeats()
        self.assertEqual(result['stuck_count'], 0)
