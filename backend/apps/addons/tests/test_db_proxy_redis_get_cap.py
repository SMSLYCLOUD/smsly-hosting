# pylint: disable=invalid-name
"""Tests for ``DatabaseProxy.redis_get`` 1MB response cap (Issue 184).

A user with a 100MB hash could OOM the backend by calling
``redis_get`` on it.  The fix caps the deserialized response at
1MB and returns ``{truncated: True, total_size: N, data: ...}``
when the cap is exceeded.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.db_proxy import DatabaseProxy
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


class RedisGetResponseCapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="redisget", email="rg@test.com", password="x",
        )
        self.service = Service.objects.create(name="rg-svc", owner=self.user)
        self.addon = Addon.objects.create(
            service=self.service,
            name="redis",
            addon_type=Addon.Type.REDIS,
            status=Addon.Status.ACTIVE,
            connection_url="redis://test:test@redis:6379/0",
        )
        self.proxy = DatabaseProxy(self.addon)

    def test_string_response_unaffected(self):
        fake = MagicMock()
        fake.type.return_value = "string"
        fake.ttl.return_value = -1
        fake.get.return_value = "value"
        with patch.object(self.proxy, 'get_connection', return_value=fake):
            result = self.proxy.redis_get("k")
        self.assertEqual(result["value"], "value")
        self.assertNotIn("truncated", result)

    def test_hash_under_cap_unaffected(self):
        fake = MagicMock()
        fake.type.return_value = "hash"
        fake.ttl.return_value = -1
        fake.hgetall.return_value = {"a": "1", "b": "2"}
        with patch.object(self.proxy, 'get_connection', return_value=fake):
            result = self.proxy.redis_get("k")
        self.assertFalse(result.get("truncated"))
        self.assertEqual(result["value"], {"a": "1", "b": "2"})

    def test_hash_over_cap_returns_truncated_envelope(self):
        fake = MagicMock()
        fake.type.return_value = "hash"
        fake.ttl.return_value = -1
        large = {f"key{i}": "x" * 2000 for i in range(2000)}
        fake.hgetall.return_value = large
        with patch.object(self.proxy, 'get_connection', return_value=fake):
            result = self.proxy.redis_get("k")
        self.assertTrue(result["truncated"])
        self.assertIn("total_size", result)
        self.assertGreater(result["total_size"], 1_048_576)
        self.assertIn("data", result)

    def test_hash_over_cap_data_smaller_than_total(self):
        fake = MagicMock()
        fake.type.return_value = "hash"
        fake.ttl.return_value = -1
        large = {f"k{i}": "x" * 2000 for i in range(2000)}
        fake.hgetall.return_value = large
        with patch.object(self.proxy, 'get_connection', return_value=fake):
            result = self.proxy.redis_get("k")
        partial = result["data"]
        self.assertLess(
            len(partial), len(large),
            "Expected the truncated dict to be smaller than the original",
        )

    def test_cap_response_size_helper_under_cap(self):
        small = {"a": "1", "b": "2"}
        value, total_size, truncated = self.proxy._cap_response_size(small)
        self.assertEqual(value, small)
        self.assertGreater(total_size, 0)
        self.assertFalse(truncated)

    def test_cap_response_size_helper_over_cap(self):
        big = {f"k{i}": "x" * 2000 for i in range(2000)}
        value, total_size, truncated = self.proxy._cap_response_size(big)
        self.assertTrue(truncated)
        self.assertGreater(total_size, 1_048_576)
        self.assertLess(len(value), len(big))
