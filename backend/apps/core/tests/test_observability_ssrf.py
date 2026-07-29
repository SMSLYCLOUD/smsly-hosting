# pylint: disable=invalid-name
"""Hermetic tests for the observability proxy SSRF / tenant-leak guards."""
from datetime import UTC
from unittest.mock import patch

from apps.core.views.observability import (
    ALLOWED_LOKI_LABELS,
    MAX_LOKI_QUERY_LENGTH,
    MAX_PROMETHEUS_QUERY_LENGTH,
    SAFE_QUERY_CHARS_RE,
    ObservabilityRateThrottle,
    _parse_prometheus_time,
    _scope_query_to_tenant,
    _user_owned_service_names,
    _validate_query_chars,
)
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

User = get_user_model()


class QueryValidationUnitTests(TestCase):
    """Pure-Python checks for the helper functions used by the proxy views."""

    def test_safe_regex_accepts_promql_examples(self):
        ok = [
            'up',
            'up{job="prometheus"}',
            'rate(http_requests_total[5m])',
            '{job="foo", level="error"}',
            'sum by (job) (rate(http_requests_total[5m]))',
            'http_requests_total{status="500"}',
        ]
        for q in ok:
            self.assertIsNotNone(
                SAFE_QUERY_CHARS_RE.match(q),
                f'Expected safe query to pass: {q!r}',
            )

    def test_safe_regex_rejects_unsafe_characters(self):
        bad = [
            'up; DROP TABLE users;',
            'up|grep',
            'up && curl evil',
            'up`whoami`',
            'up$VAR',
            'up@host',
            'up\x00null',
            'up%0d%0a',
            '../etc/passwd',
        ]
        for q in bad:
            self.assertIsNone(
                SAFE_QUERY_CHARS_RE.match(q),
                f'Expected unsafe query to be rejected: {q!r}',
            )

    def test_safe_regex_accepts_promql_selectors_with_curly_and_quote(self):
        # Valid PromQL/LogQL selectors with {, }, " must pass the regex
        # (tenant scoping handles isolation, not the regex).
        good = [
            'up{job="prometheus"}',
            '{job="prometheus"}',
            'up{tenant="other"}',  # valid PromQL; tenant scope will filter it
        ]
        for q in good:
            self.assertIsNotNone(
                SAFE_QUERY_CHARS_RE.match(q),
                f'Expected safe query to pass: {q!r}',
            )

    def test_validate_query_chars_returns_error_for_unsafe(self):
        self.assertNotEqual(_validate_query_chars('up; DROP'), '')
        self.assertNotEqual(_validate_query_chars(''), '')

    def test_validate_query_chars_returns_empty_for_safe(self):
        self.assertEqual(_validate_query_chars('up'), '')

    def test_scope_query_to_tenant_injects_into_existing_selector(self):
        out = _scope_query_to_tenant(
            '{job="prometheus"}', ['svc-a', 'svc-b'],
        )
        # The implementation escapes regex metachars in service names (e.g. `-`).
        self.assertIn(r'compose_service=~"svc\-a|svc\-b"', out)
        self.assertIn('job="prometheus"', out)

    def test_scope_query_to_tenant_wraps_plain_query(self):
        out = _scope_query_to_tenant('up', ['svc-a'])
        # Plain queries get wrapped in a selector with the tenant scope.
        self.assertTrue(
            out.startswith('{'),
            f'Expected wrapped selector, got: {out!r}',
        )
        self.assertIn(r'compose_service=~"svc\-a"', out)

    def test_scope_query_to_tenant_rejects_when_user_has_no_services(self):
        with self.assertRaises(ValueError):
            _scope_query_to_tenant('up', [])

    def test_scope_query_to_tenant_escapes_regex_metachars(self):
        out = _scope_query_to_tenant('up', ['svc.a+b'])
        self.assertIn(r'svc\.a\+b', out)

    def test_parse_prometheus_time_rejects_out_of_range(self):
        from datetime import datetime, timedelta
        too_old = (datetime.now(UTC) - timedelta(days=365)).timestamp()
        self.assertIsNone(_parse_prometheus_time(str(too_old)))
        self.assertIsNone(_parse_prometheus_time('not-a-number'))
        self.assertIsNone(_parse_prometheus_time(''))
        self.assertIsNone(_parse_prometheus_time(None))

    def test_parse_prometheus_time_accepts_recent_timestamp(self):
        from datetime import datetime, timedelta
        recent = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
        self.assertEqual(
            _parse_prometheus_time(str(recent)),
            str(recent),
        )

    def test_throttle_has_rate_baked_in(self):
        """The throttle must not depend on a settings entry that doesn't
        exist in the test environment.
        """
        t = ObservabilityRateThrottle()
        self.assertEqual(t.rate, '30/minute')
        self.assertEqual(t.num_requests, 30)
        self.assertEqual(t.duration, 60)


class ObservabilitySafetyTests(TestCase):
    """End-to-end tests that exercise the proxy views with a mocked
    Prometheus/Loki HTTP layer so no real network calls are made.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='obs-user', password='x',
        )
        self.client.force_authenticate(self.user)

    @override_settings(
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_loki_label_values_only_allows_safe_labels(self):
        safe = sorted(ALLOWED_LOKI_LABELS)
        unsafe = ['tenant', 'user', 'admin', 'password']
        for label in safe:
            with patch('apps.core.views.observability.requests.get') as gget:
                gget.return_value.status_code = 200
                gget.return_value.json.return_value = {'data': []}
                gget.return_value.raise_for_status = lambda: None
                resp = self.client.get(
                    f'/api/v1/core/observability/loki/label/{label}/values/'
                )
            self.assertNotIn(
                resp.status_code, (400, 403),
                f'Safe label {label!r} should not be rejected, '
                f'got {resp.status_code}: {resp.data}',
            )
        for label in unsafe:
            resp = self.client.get(
                f'/api/v1/core/observability/loki/label/{label}/values/'
            )
            self.assertIn(
                resp.status_code, (400, 403),
                f'Unsafe label {label!r} should be rejected, '
                f'got {resp.status_code}: {resp.data}',
            )

    @override_settings(
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_prometheus_query_rejects_unsafe_characters(self):
        resp = self.client.get(
            '/api/v1/core/observability/prometheus/query/',
            {'query': 'up; DROP TABLE metrics'},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', resp.data)

    @override_settings(
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_prometheus_query_rejects_oversize_query(self):
        big = 'a' * (MAX_PROMETHEUS_QUERY_LENGTH + 1)
        resp = self.client.get(
            '/api/v1/core/observability/prometheus/query/',
            {'query': big},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('exceeds', resp.data.get('error', ''))

    @override_settings(
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_prometheus_query_rejects_time_outside_30_days(self):
        from datetime import datetime, timedelta

        from apps.deployments.models import Service
        # Give the user a service so the tenant-scope guard does not short-circuit
        # the time validation below.
        Service.objects.create(
            name='obs-svc', owner=self.user, repository_url='', branch='main',
        )
        too_old = (datetime.now(UTC) - timedelta(days=365)).timestamp()
        with patch('apps.core.views.observability.requests.get') as gget:
            gget.return_value.status_code = 200
            gget.return_value.json.return_value = {'data': {'result': []}}
            gget.return_value.raise_for_status = lambda: None
            resp = self.client.get(
                '/api/v1/core/observability/prometheus/query/',
                {'query': 'up{job="x"}', 'time': str(too_old)},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('time', resp.data.get('error', '').lower())

    @override_settings(
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_prometheus_query_rejects_user_with_no_services(self):
        resp = self.client.get(
            '/api/v1/core/observability/prometheus/query/',
            {'query': 'up{job="x"}'},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no services', resp.data.get('error', '').lower())

    @override_settings(
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_loki_query_rejects_oversize_query(self):
        big = '{job="x"} ' + 'a' * (MAX_LOKI_QUERY_LENGTH)
        resp = self.client.get(
            '/api/v1/core/observability/loki/query/',
            {'query': big},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('exceeds', resp.data.get('error', ''))

    @override_settings(
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
    )
    def test_loki_query_injects_tenant_scope_for_user_with_service(self):
        from apps.deployments.models.core import Service
        Service.objects.create(
            owner=self.user, name='alpha', deploy_type='DOCKER',
        )
        with patch('apps.core.views.observability.requests.get') as gget:
            gget.return_value.status_code = 200
            gget.return_value.json.return_value = {
                'data': {'result': [], 'stats': {}},
            }
            gget.return_value.raise_for_status = lambda: None
            resp = self.client.get(
                '/api/v1/core/observability/loki/query/',
                {'query': '{job="x"}'},
            )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(gget.called, 'expected requests.get to be called')
        called_params = gget.call_args.kwargs.get('params') or gget.call_args.args[1]
        self.assertIn('compose_service=~"alpha"', called_params['query'])


class UserOwnedServiceNamesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='svc-owner', password='x')
        self.other = User.objects.create_user(username='svc-stranger', password='x')

    def test_returns_only_owned_service_names(self):
        from apps.deployments.models.core import Service
        Service.objects.create(owner=self.user, name='mine', deploy_type='DOCKER')
        Service.objects.create(owner=self.other, name='theirs', deploy_type='DOCKER')
        names = _user_owned_service_names(self.user)
        self.assertIn('mine', names)
        self.assertNotIn('theirs', names)
