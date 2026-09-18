"""Ops surface added for the dashboard gaps review.

Covers:
  1. PATCH /system/config/ accepts ROLLBACK_RETAIN_DEPLOYMENTS (PlatformConfig
     field exists; read path clamps, so the write path needs no clamp).
  2. Infra health carries a ``beat`` section with redbeat lock state that
     never raises when Redis is unreachable.
  3. GET /addons/shared-postgres-ha/ is admin-only and returns the shared
     pool HA payload.
  4. POST /system/beat-heal/ refuses live locks and heals stale ones.
  5. RouteFallbackView validates pages (non-empty, size cap, request-ID
     contract) and 404s without the container.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.addons.views.crud import AddonViewSet
from apps.core.views.system import BeatHealView, RouteFallbackView, SystemConfigView

User = get_user_model()


class RollbackRetainFieldTests(SimpleTestCase):
    def test_patch_accepts_retain_count(self):
        self.assertIn('ROLLBACK_RETAIN_DEPLOYMENTS', SystemConfigView._PC_FIELDS)
        field, cast = SystemConfigView._PC_FIELDS['ROLLBACK_RETAIN_DEPLOYMENTS']
        self.assertEqual(field, 'rollback_retain_deployments')
        self.assertIs(cast, int)


class BeatStatusTests(SimpleTestCase):
    def test_beat_status_shape_without_redis(self):
        view = SystemConfigView()
        with mock.patch(
            'config.redis_sentinel.get_master_connection',
            side_effect=ConnectionError('no redis'),
        ):
            result = view._get_beat_status()
        self.assertEqual(result['scheduler'], 'redbeat')
        self.assertGreaterEqual(result['lock_timeout'], 300)
        self.assertLessEqual(result['lock_timeout'], 3600)
        self.assertIn('lock_ttl', result)
        self.assertIn('healthy', result)

    def test_beat_status_reads_lock_ttl(self):
        view = SystemConfigView()
        conn = mock.Mock()
        conn.ttl.return_value = 420
        with mock.patch(
            'config.redis_sentinel.SENTINEL_ENABLED', True,
        ), mock.patch(
            'config.redis_sentinel.get_master_connection', return_value=conn,
        ):
            result = view._get_beat_status()
        self.assertEqual(result['lock_ttl'], 420)
        self.assertTrue(result['healthy'])


class SharedPostgresHaEndpointTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = AddonViewSet.as_view({'get': 'shared_postgres_ha'})
        self.user = User.objects.create_user(username="sharedha", password="x")
        self.admin = User.objects.create_superuser(
            username="sharedhaadmin", password="x", email="a@x.io",
        )

    def test_non_admin_forbidden(self):
        request = self.factory.get('/addons/shared-postgres-ha/')
        force_authenticate(request, user=self.user)
        response = self.view(request)
        self.assertEqual(response.status_code, 403)

    def test_admin_returns_pool_state(self):
        payload = {
            'state': 'HEALTHY', 'primary': 'smsly-shared-postgres',
            'standby': 'smsly-shared-postgres-replica', 'lag_seconds': 0.0,
        }
        request = self.factory.get('/addons/shared-postgres-ha/')
        force_authenticate(request, user=self.admin)
        with mock.patch(
            'apps.addons.services.shared_postgres.shared_ha_status',
            return_value=payload,
        ):
            response = self.view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['state'], 'HEALTHY')


OLD_STARTED_AT = '2026-01-01T00:00:00.000000000Z'


class BeatHealGateTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = BeatHealView.as_view()
        self.admin = User.objects.create_superuser(
            username="beathealadmin", password="x", email="b@x.io",
        )

    def _post(self):
        request = self.factory.post('/system/beat-heal/')
        force_authenticate(request, user=self.admin)
        return self.view(request)

    def _docker_ok(self, mock_docker):
        def _fake(*args, **kwargs):
            verb = args[0] if args else ''
            if verb == 'inspect':
                return {'output': OLD_STARTED_AT}
            if verb == 'ps':
                return {'output': 'smsly-hosting-celery-beat-1'}
            if verb == 'restart':
                return {'output': 'smsly-hosting-celery-beat-1'}
            return {'error': 'unexpected'}
        mock_docker.side_effect = _fake

    def test_live_lock_refused(self):
        with mock.patch.object(BeatHealView, '_docker') as mock_docker, \
             mock.patch.object(BeatHealView, '_dispatch_count_15m', return_value=0), \
             mock.patch.object(BeatHealView, '_lock_ttl', return_value=590):
            self._docker_ok(mock_docker)
            response = self._post()
        self.assertEqual(response.status_code, 409)
        self.assertIn('live', response.data['error'])

    def test_stale_lock_healed(self):
        with mock.patch.object(BeatHealView, '_docker') as mock_docker, \
             mock.patch.object(BeatHealView, '_dispatch_count_15m', return_value=0), \
             mock.patch.object(BeatHealView, '_lock_ttl', return_value=42), \
             mock.patch.object(BeatHealView, '_del_lock', return_value=True):
            self._docker_ok(mock_docker)
            response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'ok')
        self.assertTrue(any('restarted beat' in a for a in response.data['actions']))

    def test_dispatching_beat_refused(self):
        with mock.patch.object(BeatHealView, '_docker') as mock_docker, \
             mock.patch.object(BeatHealView, '_dispatch_count_15m', return_value=3), \
             mock.patch.object(BeatHealView, '_lock_ttl', return_value=10):
            self._docker_ok(mock_docker)
            response = self._post()
        self.assertEqual(response.status_code, 409)


class RouteFallbackValidationTests(TestCase):
    def test_rejects_empty(self):
        view = RouteFallbackView()
        self.assertIn('non-empty', view._validate('index.html', '   '))

    def test_rejects_missing_request_id(self):
        view = RouteFallbackView()
        self.assertIn('request-ID', view._validate('index.html', '<html>hi</html>'))

    def test_rejects_oversize(self):
        view = RouteFallbackView()
        big = '<html>http.request.uuid' + ('x' * view.MAX_BYTES) + '</html>'
        self.assertIn('exceeds', view._validate('index.html', big))

    def test_accepts_valid_page(self):
        view = RouteFallbackView()
        self.assertEqual(view._validate('index.html', '<html>http.request.uuid</html>'), '')

    def test_get_404_without_container(self):
        admin = User.objects.create_superuser(
            username="fb404admin", password="x", email="f@x.io")
        view = RouteFallbackView.as_view()
        request = APIRequestFactory().get('/system/route-fallback/')
        force_authenticate(request, user=admin)
        with mock.patch.object(RouteFallbackView, '_container', return_value=None):
            response = view(request)
        self.assertEqual(response.status_code, 404)
