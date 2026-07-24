# pylint: disable=invalid-name
"""
Regression tests for Issue 24 (db_proxy query TOCTOU lock).

The ``DatabaseProxy.query`` method must:
  * acquire a per-addon Redis lock for the duration of the
    query so two simultaneous calls cannot both pass the
    throttle check and both issue queries against the same
    addon;
  * raise ``ValueError`` when the lock cannot be acquired;
  * always release the lock, even when the query raises.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from apps.addons.services.db_proxy import DatabaseProxy
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


class DBProxyLockTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="dplock", email="dplock@test.com", password="x",
        )
        self.service = Service.objects.create(name="lock-svc", owner=self.user)
        self.addon = Addon.objects.create(
            service=self.service,
            name="db",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
            connection_url="postgresql://test:test@db:5432/test",
        )
        self.proxy = DatabaseProxy(self.addon)

    def test_lock_acquired_and_released_on_success(self):
        with patch.object(self.proxy, "_execute_readonly") as mock_exec:
            mock_exec.return_value = {"columns": ["a"], "rows": [[1]], "count": 1}
            self.proxy.query(
                "SELECT 1", addon=self.addon, user=self.user,
            )
        self.assertIsNone(cache.get(f"db_proxy_lock:{self.addon.id}"))
        mock_exec.assert_called_once()

    def test_concurrent_query_rejected(self):
        cache.set(f"db_proxy_lock:{self.addon.id}", "1", timeout=30)
        with self.assertRaises(ValueError) as cm:
            self.proxy.query(
                "SELECT 1", addon=self.addon, user=self.user,
            )
        self.assertIn("in progress", str(cm.exception).lower())

    def test_lock_released_on_query_failure(self):
        with patch.object(
            self.proxy, "_execute_readonly",
            side_effect=RuntimeError("DB went away"),
        ), self.assertRaises(RuntimeError):
            self.proxy.query(
                "SELECT 1", addon=self.addon, user=self.user,
            )
        self.assertIsNone(cache.get(f"db_proxy_lock:{self.addon.id}"))

    def test_lock_released_on_ownership_failure(self):
        other = User.objects.create_user(
            username="dplock-other", email="dplock-other@test.com", password="x",
        )
        with self.assertRaises(PermissionError):
            self.proxy.query(
                "SELECT 1", addon=self.addon, user=other,
            )
        # The validation runs BEFORE the lock, so the lock must
        # not have been acquired (and the cache must be empty).
        self.assertIsNone(cache.get(f"db_proxy_lock:{self.addon.id}"))
