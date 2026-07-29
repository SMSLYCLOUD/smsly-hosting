# pylint: disable=invalid-name
"""
Regression tests for the dashboard-unblocking fixes (Batch H).

Covers:
  1. ServiceViewSet: GET (list/retrieve) is NOT subject to the
     deployment-burst throttle (3/minute). The dashboard fires
     many GETs per page; throttling them at 3/min breaks the UI.
  2. ServiceViewSet: POST/PATCH/DELETE (write) IS still subject
     to the burst throttle, so abusive deploy/restart/stop
     actions remain rate-limited.
  3. DeploymentViewSet: same pattern (safe GETs free, writes
     throttled).
  4. Frontend-compat aliases at /api/v1/dashboard/overview/,
     /api/v1/system/health/, /api/v1/system/resources/ all
     resolve to the canonical core-app views.
"""
from unittest.mock import patch

from apps.deployments.models.core import Service
from apps.core.rate_limiting import (
    BurstRateThrottle,
    DeploymentRateThrottle,
)
from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIClient


class ServiceViewSetThrottleTests(TestCase):
    """GETs on /services/ must NOT hit the deployment-burst
    throttle (3/minute). The dashboard renders 4-20 GETs per page
    and the 3/min cap was 429ing the user out of the gate.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = self._make_user()
        self.client.force_authenticate(self.user)

    @staticmethod
    def _make_user(username='alice'):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user(
            username=username, password='x',
        )

    def _bypass_throttle(self):
        # The default 'user' throttle is still active. Patch it
        # to no-op so it doesn't trip first.
        from apps.core.middleware.ratelimit import RateLimitMiddleware
        return patch.object(
            RateLimitMiddleware, '__call__',
            lambda self, request: self.get_response(request),
        )

    def test_get_list_is_not_burst_throttled(self):
        """GET /services/ (list) is a safe method and must not
        be subject to the 3/minute burst throttle.
        """
        with self._bypass_throttle(), \
             patch.object(BurstRateThrottle, 'allow_request',
                          return_value=False) as burst_mock:
            # Fire 5 GETs; if the throttle were applied the
            # 2nd-5th would 429.
            for _ in range(5):
                resp = self.client.get('/api/v1/services/')
                self.assertIn(
                    resp.status_code, (200, 403),
                    f'GET /services/ should not be burst-throttled, '
                    f'got status {resp.status_code}',
                )
            # The burst throttle must never have been consulted
            # for any of those GETs.
            self.assertFalse(
                burst_mock.called,
                'BurstRateThrottle.allow_request was called for a '
                'GET — it must only fire on write methods.',
            )

    def test_get_retrieve_is_not_burst_throttled(self):
        """GET /services/<id>/ is also safe and must not
        trip the burst throttle.
        """
        service = Service.objects.create(
            owner=self.user, name='demo-svc', deploy_type='DOCKER',
        )
        with self._bypass_throttle(), \
             patch.object(BurstRateThrottle, 'allow_request',
                          return_value=False) as burst_mock:
            for _ in range(5):
                resp = self.client.get(f'/api/v1/services/{service.id}/')
                self.assertIn(
                    resp.status_code, (200, 403),
                    f'GET /services/<id>/ should not be burst-throttled, '
                    f'got status {resp.status_code}',
                )
            self.assertFalse(burst_mock.called)

    def test_get_throttles_returns_empty_for_safe_methods(self):
        """Direct unit test of get_throttles(): safe methods
        must return [] (no throttles); write methods must
        return the burst + deployment throttles.
        """
        from apps.deployments.views.service import ServiceViewSet
        from rest_framework.test import APIRequestFactory
        factory = APIRequestFactory()
        for method in ('get', 'head', 'options'):
            request = getattr(factory, method)('/api/v1/services/')
            request.user = self.user
            view = ServiceViewSet()
            view.request = request
            view.action = 'list'
            view.kwargs = {}
            throttles = view.get_throttles()
            self.assertEqual(
                throttles, [],
                f'ServiceViewSet.get_throttles() must return [] for '
                f'{method.upper()}, got {[t.__class__.__name__ for t in throttles]}',
            )
        for method in ('post', 'put', 'patch', 'delete'):
            request = getattr(factory, method)('/api/v1/services/')
            request.user = self.user
            view = ServiceViewSet()
            view.request = request
            view.action = 'create' if method == 'post' else 'update'
            view.kwargs = {}
            throttles = view.get_throttles()
            classes = {t.__class__ for t in throttles}
            self.assertIn(
                BurstRateThrottle, classes,
                f'BurstRateThrottle must be applied on {method.upper()}, '
                f'got {[t.__class__.__name__ for t in throttles]}',
            )
            self.assertIn(
                DeploymentRateThrottle, classes,
                f'DeploymentRateThrottle must be applied on {method.upper()}, '
                f'got {[t.__class__.__name__ for t in throttles]}',
            )


class DeploymentViewSetThrottleTests(TestCase):
    """Same pattern for DeploymentViewSet (Activity Feed,
    Intelligence page poll for build logs, etc.).
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username='bob', password='x',
        )
        self.client.force_authenticate(self.user)

    def test_get_throttles_returns_empty_for_safe_methods(self):
        from apps.deployments.views.deployment import DeploymentViewSet
        from rest_framework.test import APIRequestFactory
        factory = APIRequestFactory()
        for method in ('get', 'head', 'options'):
            request = getattr(factory, method)('/api/v1/deployments/')
            request.user = self.user
            view = DeploymentViewSet()
            view.request = request
            view.action = 'list'
            view.kwargs = {}
            throttles = view.get_throttles()
            self.assertEqual(
                throttles, [],
                f'DeploymentViewSet.get_throttles() must return [] for '
                f'{method.upper()}, got {[t.__class__.__name__ for t in throttles]}',
            )
        for method in ('post', 'put', 'patch', 'delete'):
            request = getattr(factory, method)('/api/v1/deployments/')
            request.user = self.user
            view = DeploymentViewSet()
            view.request = request
            view.action = 'create' if method == 'post' else 'update'
            view.kwargs = {}
            throttles = view.get_throttles()
            classes = {t.__class__ for t in throttles}
            self.assertIn(
                BurstRateThrottle, classes,
                f'BurstRateThrottle must be applied on {method.upper()}, '
                f'got {[t.__class__.__name__ for t in throttles]}',
            )


class FrontendCompatAliasTests(TestCase):
    """The frontend (lib/api.ts) calls some endpoints at the
    root of /api/v1/ (e.g. /api/v1/dashboard/overview/) but the
    canonical routes are mounted under /api/v1/core/. These
    tests verify the alias routes resolve to the canonical
    view function.
    """

    def test_dashboard_overview_alias_resolves(self):
        from apps.core.views import DashboardOverviewView
        match = resolve('/api/v1/dashboard/overview/')
        self.assertIs(
            match.func.view_class, DashboardOverviewView,
            '/api/v1/dashboard/overview/ must alias to '
            'apps.core.views.DashboardOverviewView (frontend compat).',
        )

    def test_system_health_alias_resolves(self):
        from config.health import health_check
        match = resolve('/api/v1/system/health/')
        self.assertIs(
            match.func, health_check,
            '/api/v1/system/health/ must alias to '
            'config.health.health_check (frontend compat).',
        )

    def test_system_resources_alias_resolves(self):
        from apps.core.views import SystemResourcesView
        match = resolve('/api/v1/system/resources/')
        self.assertIs(
            match.func.view_class, SystemResourcesView,
            '/api/v1/system/resources/ must alias to '
            'apps.core.views.SystemResourcesView (frontend compat).',
        )


class EnvVarDetailActionTests(TestCase):
    """GET /services/{id}/env_vars/{var_id}/ must work — the
    frontend ``getEnvVarValue`` (api.ts:591) calls it to reveal
    a secret. The previous decorator was methods=['delete','patch']
    which returned 405 for GET, breaking the secret-reveal flow.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username='alice', password='x',
        )
        self.client.force_authenticate(self.user)

    def test_env_var_detail_accepts_get(self):
        from apps.deployments.models.core import (
            EnvironmentVariable,
            Service,
        )
        service = Service.objects.create(
            owner=self.user, name='svc-1', deploy_type='DOCKER',
        )
        env = EnvironmentVariable.objects.create(
            service=service, key='API_KEY', value='secret-xyz',
            is_secret=True,
        )
        resp = self.client.get(
            f'/api/v1/services/{service.id}/env_vars/{env.id}/'
        )
        self.assertIn(
            resp.status_code, (200, 403),
            f'GET /env_vars/<id>/ must be allowed (200 or 403), '
            f'got {resp.status_code}.',
        )

    def test_env_var_detail_still_supports_delete(self):
        from apps.deployments.models.core import (
            EnvironmentVariable,
            Service,
        )
        service = Service.objects.create(
            owner=self.user, name='svc-2', deploy_type='DOCKER',
        )
        env = EnvironmentVariable.objects.create(
            service=service, key='OLD', value='old-value',
        )
        resp = self.client.delete(
            f'/api/v1/services/{service.id}/env_vars/{env.id}/'
        )
        self.assertIn(
            resp.status_code, (204, 403),
            f'DELETE /env_vars/<id>/ must continue to work, '
            f'got {resp.status_code}.',
        )
        self.assertFalse(
            EnvironmentVariable.objects.filter(id=env.id).exists(),
            'EnvVar must be deleted after DELETE.',
        )


class EcosystemBulkEnvAliasTests(TestCase):
    """POST /api/v1/ecosystem/bulk-update-environment/ must
    resolve to the same view that handles
    /api/v1/cloud/ecosystem/bulk-env/.
    """

    def test_bulk_env_alias_resolves(self):
        from django.urls import resolve
        match = resolve('/api/v1/ecosystem/bulk-update-environment/')
        # The view is a method-bound view, not a class.
        # Verify the resolved view dispatches to the same action.
        self.assertTrue(
            hasattr(match.func, 'actions'),
            'alias must resolve to a DRF view with actions',
        )
        self.assertIn(
            'post', match.func.actions,
            'alias must accept POST',
        )


class PreferencesAliasTests(TestCase):
    """GET /api/v1/preferences/ must resolve to the NotificationPreferenceViewSet.
    """

    def test_preferences_alias_resolves(self):
        from django.urls import resolve
        match = resolve('/api/v1/preferences/')
        self.assertTrue(hasattr(match.func, 'actions'))
        self.assertIn('get', match.func.actions)
        # The PATCH endpoint is the detail URL: /preferences/<id>/
        match_detail = resolve('/api/v1/preferences/1/')
        self.assertIn('patch', match_detail.func.actions)


class DefaultThrottleRateTests(TestCase):
    """The default 'user' and 'anon' throttles must be
    relaxed enough that the dashboard's per-page burst of
    4-20 GETs + auto-refresh does not trip them.

    The previous values (5000/hour, 200/hour) 429'd the
    dashboard out of the gate.
    """

    def test_user_throttle_is_generous(self):
        from rest_framework.settings import api_settings
        rates = api_settings.DEFAULT_THROTTLE_RATES
        self.assertIn('user', rates)
        # Parse the rate string: '<num>/<period>'
        num, period = rates['user'].split('/')
        num = int(num)
        # 'hour' period: 10000/hour is a reasonable floor
        # that still protects against abuse.
        if period == 'hour':
            self.assertGreaterEqual(
                num, 10000,
                f"Default 'user' throttle {rates['user']} is too tight "
                f"for the dashboard's per-page burst of 4-20 GETs.",
            )
        elif period == 'min':
            self.assertGreaterEqual(
                num, 1000,
                f"Default 'user' throttle {rates['user']} is too tight.",
            )
        elif period == 'day':
            self.assertGreaterEqual(
                num, 100000,
                f"Default 'user' throttle {rates['user']} is too tight.",
            )

    def test_anon_throttle_is_generous(self):
        from rest_framework.settings import api_settings
        rates = api_settings.DEFAULT_THROTTLE_RATES
        self.assertIn('anon', rates)
        num, period = rates['anon'].split('/')
        num = int(num)
        if period == 'hour':
            self.assertGreaterEqual(
                num, 1000,
                f"Default 'anon' throttle {rates['anon']} is too tight "
                f"for monitoring probes and unauthenticated reads.",
            )

    def test_api_rate_limit_middleware_is_generous(self):
        from django.conf import settings
        self.assertGreaterEqual(
            settings.API_RATE_LIMIT, 5000,
            f"API_RATE_LIMIT={settings.API_RATE_LIMIT} is too tight; "
            f"bumped in Batch H to 10000 to avoid 429-ing the dashboard.",
        )

    def test_deployment_burst_is_user_friendly(self):
        # SECURITY (Batch I cont): 'deployment_burst' was 3/minute
        # which was too tight for normal interactive work
        # (creating a service, deploying, then doing it again
        # 30 seconds later). Bumped through 30 → 200 →
        # 5000/minute so operators can do create/deploy/verify
        # /delete cycles without hitting 429, and so the cached
        # counter from a previous (tighter) rate doesn't
        # accidentally throttle them on a code deploy.
        from rest_framework.settings import api_settings
        rates = api_settings.DEFAULT_THROTTLE_RATES
        num, period = rates['deployment_burst'].split('/')
        num = int(num)
        self.assertEqual(
            period, 'minute',
            f"deployment_burst={rates['deployment_burst']} should be "
            f"per-minute so the throttle resets quickly.",
        )
        self.assertGreaterEqual(
            num, 100,
            f"deployment_burst={rates['deployment_burst']} is too tight "
            f"for interactive use; bumped in Batch I cont to 5000/minute.",
        )


class AliasesBeforeBroadIncludeTests(TestCase):
    """The frontend-compat aliases (e.g. /api/v1/dashboard/overview/)
    MUST be registered before the broad
    ``path('api/v1/', include('apps.deployments.urls'))`` so the
    deployments urls do not consume and 404 those paths first.
    """

    def test_dashboard_alias_actually_routes(self):
        from django.urls import resolve
        match = resolve('/api/v1/dashboard/overview/')
        # The canonical view, NOT a 404 from the deployments include.
        self.assertTrue(
            match.func.view_class.__name__ == 'DashboardOverviewView',
            f'/api/v1/dashboard/overview/ resolved to '
            f'{match.func.view_class.__name__}; expected DashboardOverviewView. '
            f'If it resolved to a catch-all in deployments.urls, the alias '
            f'is registered after the broad include and never reached.',
        )
