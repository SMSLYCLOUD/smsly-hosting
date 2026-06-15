"""
Regression tests for Finding #162 (positive token cache).

``TerminalConsumer._authenticate_token`` previously queried the
``Token`` table on every WebSocket connect. A short-lived positive
cache (5s TTL) on the (token -> user_id) pair now sits in front of
the database so high-frequency connect storms do not translate into
a DB hit per connection.

The test invokes the underlying coroutine through
``async_to_sync`` (the same wrapper channels uses internally) and
asserts:

  * the first call populates the positive cache;
  * the second call (within 5s) does not hit the DB.
"""
import hashlib

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from rest_framework.authtoken.models import Token

from apps.deployments.consumers import TerminalConsumer


User = get_user_model()


class Finding162PositiveTokenCacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="ws-cache-162", password="p", is_superuser=True,
        )
        from django.apps import apps
        deployments_app = apps.get_app_config("deployments")
        for model in deployments_app.get_models():
            ct, _ = ContentType.objects.get_or_create(
                app_label=model._meta.app_label,
                model=model._meta.model_name,
            )
            perm, _ = Permission.objects.get_or_create(
                content_type=ct,
                codename=f"view_{model._meta.model_name}",
            )
            cls.user.user_permissions.add(perm)
        cls.token = Token.objects.create(user=cls.user)

    def setUp(self):
        cache.clear()

    def _positive_cache_key(self, key):
        return "token_user:" + hashlib.sha256(key.encode()).hexdigest()

    def _build_consumer(self):
        consumer = TerminalConsumer()
        consumer.user = None
        return consumer

    def test_first_call_populates_positive_cache(self):
        consumer = self._build_consumer()
        result = async_to_sync(consumer._authenticate_token)(self.token.key)
        self.assertEqual(result, self.user)
        cached = cache.get(self._positive_cache_key(self.token.key))
        self.assertEqual(cached, self.user.id)

    def test_second_call_within_ttl_skips_db(self):
        consumer = self._build_consumer()
        first = async_to_sync(consumer._authenticate_token)(self.token.key)
        self.assertEqual(first, self.user)

        from unittest.mock import patch
        with patch.object(Token.objects, "get") as mock_get:
            mock_get.side_effect = AssertionError("DB should not be hit on second call")
            second = async_to_sync(consumer._authenticate_token)(self.token.key)
        self.assertEqual(second, self.user)

    def test_invalid_token_does_not_populate_positive_cache(self):
        consumer = self._build_consumer()
        result = async_to_sync(consumer._authenticate_token)("0" * 40)
        self.assertIsNone(result)
        cached = cache.get(self._positive_cache_key("0" * 40))
        self.assertIsNone(cached)
