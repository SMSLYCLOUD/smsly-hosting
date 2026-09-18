# pylint: disable=invalid-name
"""Shared Postgres HA watchdog: heartbeat, self-heal, auto-failover gates.

The automatic path must be conservative: promote only on sustained
primary death with a recently-healthy standby outside cooldown.
Everything ambiguous alerts without touching topology.
"""
import time
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.deployments.tests.conftest import TEST_CACHES

from apps.addons.tasks import ha_watchdog as wd


def _healthy(lag=0.05):
    return {
        'state': 'HEALTHY', 'primary': 'smsly-shared-postgres',
        'standby': 'smsly-shared-postgres-replica', 'lag_seconds': lag,
    }


def _down():
    return {
        'state': 'DOWN', 'primary': None,
        'standby': 'smsly-shared-postgres-replica', 'lag_seconds': None,
    }


@override_settings(CACHES=TEST_CACHES)
class SharedHaWatchdogTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _run(self):
        return wd.check_shared_postgres_ha_task()

    def test_healthy_records_heartbeat(self):
        with patch('apps.addons.services.shared_postgres.shared_ha_status', return_value=_healthy()):
            result = self._run()
        self.assertEqual(result['action'], 'none')
        self.assertGreater(cache.get(wd._SHARED_PG_HEALTHY_KEY) or 0, 0)
        self.assertEqual(cache.get(wd._SHARED_PG_DOWN_KEY), 0)

    def test_standalone_restores_standby(self):
        with patch(
            'apps.addons.services.shared_postgres.shared_ha_status',
            return_value={'state': 'STANDALONE',
                          'primary': 'smsly-shared-postgres',
                          'standby': None, 'lag_seconds': None},
        ), patch('apps.addons.services.shared_postgres.ensure_shared_standby') as mock_ensure:
            result = self._run()
        self.assertEqual(result['action'], 'standby-restored')
        mock_ensure.assert_called_once_with()

    def test_down_counts_up_before_probes_met(self):
        with patch('apps.addons.services.shared_postgres.shared_ha_status', return_value=_down()), \
                patch('apps.addons.services.shared_postgres.promote_shared_standby') as mock_promote:
            first = self._run()
            second = self._run()
        self.assertEqual(first['action'], 'none')
        self.assertEqual(second['action'], 'none')
        mock_promote.assert_not_called()

    def test_down_promotes_with_fresh_standby(self):
        cache.set(wd._SHARED_PG_DOWN_KEY, wd._SHARED_PG_PROBES - 1, timeout=3600)
        cache.set(wd._SHARED_PG_HEALTHY_KEY, time.time(), timeout=3600)
        with patch('apps.addons.services.shared_postgres.shared_ha_status', return_value=_down()), \
                patch('apps.addons.services.shared_postgres.promote_shared_standby',
                             return_value='smsly-shared-postgres') as mock_promote, \
                patch('apps.addons.services.shared_postgres.ensure_shared_standby') as mock_reseed, \
                patch.object(wd, '_shared_pg_notify'):
            result = self._run()
        self.assertEqual(result['action'], 'failed-over')
        mock_promote.assert_called_once_with()
        mock_reseed.assert_called_once_with(reseed=True)

    def test_down_stale_standby_alerts_without_promote(self):
        cache.set(wd._SHARED_PG_DOWN_KEY, wd._SHARED_PG_PROBES - 1, timeout=3600)
        cache.set(wd._SHARED_PG_HEALTHY_KEY,
                  time.time() - wd._SHARED_PG_HEALTHY_WINDOW_S - 60, timeout=7200)
        with patch('apps.addons.services.shared_postgres.shared_ha_status', return_value=_down()), \
                patch('apps.addons.services.shared_postgres.promote_shared_standby') as mock_promote, \
                patch.object(wd, '_shared_pg_notify') as mock_notify:
            result = self._run()
        self.assertEqual(result['action'], 'alerted')
        self.assertEqual(result['reason'], 'stale-standby')
        mock_promote.assert_not_called()
        mock_notify.assert_called_once()

    def test_down_on_cooldown_alerts_without_promote(self):
        cache.set(wd._SHARED_PG_DOWN_KEY, wd._SHARED_PG_PROBES - 1, timeout=3600)
        cache.set(wd._SHARED_PG_HEALTHY_KEY, time.time(), timeout=3600)
        cache.set(wd._SHARED_PG_FAILOVER_KEY, time.time(), timeout=7200)
        with patch('apps.addons.services.shared_postgres.shared_ha_status', return_value=_down()), \
                patch('apps.addons.services.shared_postgres.promote_shared_standby') as mock_promote, \
                patch.object(wd, '_shared_pg_notify'):
            result = self._run()
        self.assertEqual(result['action'], 'alerted')
        self.assertEqual(result['reason'], 'cooldown')
        mock_promote.assert_not_called()

    def test_degraded_never_touches_topology(self):
        with patch(
            'apps.addons.services.shared_postgres.shared_ha_status',
            return_value={'state': 'DEGRADED',
                          'primary': 'smsly-shared-postgres',
                          'standby': 'smsly-shared-postgres-replica',
                          'lag_seconds': None},
        ), patch('apps.addons.services.shared_postgres.promote_shared_standby') as mock_promote, \
                patch('apps.addons.services.shared_postgres.ensure_shared_standby') as mock_ensure:
            result = self._run()
        self.assertEqual(result['action'], 'none')
        mock_promote.assert_not_called()
        mock_ensure.assert_not_called()
