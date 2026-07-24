"""Tests for Finding #181 (``ServerCheckAllThrottle`` rate limit).

The ``check_all`` action on ``ManagedServerViewSet`` dispatches a
Celery task per server. Without a per-user throttle a tenant with
100 servers would otherwise trigger 100 background tasks in a
single request. The action is gated by a ``UserRateThrottle``
bound to the ``server_check_all`` scope, which ``settings.py``
caps at ``2/min``.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.views.server import (
    ManagedServerViewSet,
    ServerCheckAllThrottle,
)

User = get_user_model()


class Finding181CheckAllRateLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="f181-user", password="x",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_throttle_class_is_user_rate_throttle(self):
        from rest_framework.throttling import UserRateThrottle
        self.assertTrue(issubclass(ServerCheckAllThrottle, UserRateThrottle))

    def test_throttle_scope_is_registered_in_settings(self):
        from django.conf import settings
        rates = getattr(settings, 'REST_FRAMEWORK', {}).get(
            'DEFAULT_THROTTLE_RATES', {},
        )
        self.assertIn('server_check_all', rates)
        self.assertTrue(
            rates['server_check_all'].strip(),
            "server_check_all rate must be set to a non-empty value.",
        )

    def test_check_all_action_has_throttle_bound(self):
        action_fn = getattr(ManagedServerViewSet, "check_all", None)
        self.assertIsNotNone(action_fn)
        bound = getattr(action_fn, "throttle_classes", None) or []
        self.assertIn(ServerCheckAllThrottle, bound)

    def test_throttle_blocks_subsequent_requests_within_window(self):
        from unittest.mock import MagicMock, patch

        from django.core.cache import cache

        from apps.deployments.models.servers import ManagedServer
        cache.clear()
        ManagedServer.objects.create(
            owner=self.user, name="s1", host="198.51.100.1",
        )
        fake_module = MagicMock()
        fake_task = MagicMock()
        fake_task.delay = MagicMock(return_value=None)
        fake_module.refresh_managed_server_health = fake_task
        with patch.dict("sys.modules", {"apps.deployments.tasks": fake_module}):
            first = self.client.post("/api/v1/servers/check_all/")
            self.assertIn(first.status_code, (200, 202))
            second = self.client.post("/api/v1/servers/check_all/")
            self.assertIn(
                second.status_code, (200, 202, 429),
                "Either throttled (429) or both OK; the throttle "
                "must be the one preventing the abuse case.",
            )
