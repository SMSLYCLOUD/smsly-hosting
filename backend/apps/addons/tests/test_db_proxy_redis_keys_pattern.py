# pylint: disable=invalid-name
"""Tests for ``DatabaseProxy.redis_keys`` pattern validation (Issue 128).

A user with ``pattern='*'`` could enumerate every key in the
addon's Redis.  The fix requires a pattern that contains at
least 2 non-wildcard characters.
"""
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.db_proxy import DatabaseProxy
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


class RedisKeysPatternValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="rediskeys", email="rk@test.com", password="x",
        )
        self.service = Service.objects.create(name="rk-svc", owner=self.user)
        self.addon = Addon.objects.create(
            service=self.service,
            name="redis",
            addon_type=Addon.Type.REDIS,
            status=Addon.Status.ACTIVE,
            connection_url="redis://test:test@redis:6379/0",
        )
        self.proxy = DatabaseProxy(self.addon)

    def test_rejects_empty_pattern(self):
        with self.assertRaises(ValueError):
            self.proxy.redis_keys(pattern="")

    def test_rejects_whitespace_pattern(self):
        with self.assertRaises(ValueError):
            self.proxy.redis_keys(pattern="   ")

    def test_rejects_star_only(self):
        with self.assertRaises(ValueError):
            self.proxy.redis_keys(pattern="*")

    def test_rejects_question_mark_only(self):
        with self.assertRaises(ValueError):
            self.proxy.redis_keys(pattern="?")

    def test_rejects_single_wildcard_char(self):
        with self.assertRaises(ValueError):
            self.proxy.redis_keys(pattern="a*")

    def test_allows_class_wildcard_with_prefix(self):
        fake_redis = MagicMock()
        fake_redis.scan_iter.return_value = iter([])
        with self._patched_redis(fake_redis):
            keys = self.proxy.redis_keys(pattern="us[a-z]*")
            self.assertEqual(keys, [])

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            self.proxy.redis_keys(pattern=None)

    def test_allows_app_prefix_pattern(self):
        fake_redis = MagicMock()
        fake_redis.scan_iter.return_value = iter(["app:1", "app:2"])
        with self._patched_redis(fake_redis):
            keys = self.proxy.redis_keys(pattern="app:*")
            self.assertEqual(keys, ["app:1", "app:2"])
            fake_redis.scan_iter.assert_called_once()

    def test_allows_namespace_wildcard(self):
        fake_redis = MagicMock()
        fake_redis.scan_iter.return_value = iter(["users:1"])
        with self._patched_redis(fake_redis):
            keys = self.proxy.redis_keys(pattern="users:*")
            self.assertEqual(keys, ["users:1"])

    def _patched_redis(self, fake_redis):
        from unittest.mock import patch
        return patch.object(
            self.proxy, 'get_connection', return_value=fake_redis
        )
