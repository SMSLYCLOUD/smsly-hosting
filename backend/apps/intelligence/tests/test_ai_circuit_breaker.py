"""Tests for the AI provider fast-trip circuit breaker + bounded test-prompt."""
from unittest import mock

import httpx
from django.test import TestCase

from apps.intelligence.providers import base as ai_base
from apps.intelligence.providers import queries as ai_queries


class CircuitBreakerFastTripTests(TestCase):
    def setUp(self):
        # Reset in-memory breaker state between tests
        ai_base._provider_failures.clear()
        ai_base._provider_circuit_open_until.clear()
        ai_base._provider_connection_failures.clear()

    def test_two_connection_errors_trip_circuit(self):
        pid = "localllm"
        conn_exc = httpx.ConnectError("Connection refused")
        ai_base._record_provider_failure(pid, connection_error=True)
        self.assertFalse(ai_base._is_circuit_open(pid))
        ai_base._record_provider_failure(pid, connection_error=True)
        self.assertTrue(ai_base._is_circuit_open(pid))

    def test_http_errors_need_five_strikes(self):
        pid = "flaky-http"
        for _ in range(4):
            ai_base._record_provider_failure(pid, connection_error=False)
        self.assertFalse(ai_base._is_circuit_open(pid))
        ai_base._record_provider_failure(pid, connection_error=False)
        self.assertTrue(ai_base._is_circuit_open(pid))

    def test_connection_classification(self):
        self.assertTrue(ai_queries._is_connection_error(httpx.ConnectError("x")))
        self.assertTrue(ai_queries._is_connection_error(ConnectionError("x")))
        self.assertTrue(ai_queries._is_connection_error(OSError("x")))
        # HTTP errors are NOT connection errors
        req = httpx.Request("POST", "https://x/api")
        resp = httpx.Response(status_code=500, request=req)
        self.assertFalse(ai_queries._is_connection_error(
            httpx.HTTPStatusError("boom", request=req, response=resp)
        ))
        self.assertFalse(ai_queries._is_connection_error(ValueError("x")))

    def test_record_failure_for_classifies(self):
        pid = "conn-prov"
        ai_queries._record_failure_for(pid, httpx.ConnectError("refused"))
        ai_queries._record_failure_for(pid, httpx.ConnectError("refused"))
        self.assertTrue(ai_base._is_circuit_open(pid))

    def test_open_circuit_skips_provider(self):
        # A provider with an open circuit must be filtered out of the
        # configured list BEFORE any HTTP attempt is made.
        class _P:
            id = "dead"
            @staticmethod
            def name():
                return "dead"
            @staticmethod
            def is_configured():
                return True

        ai_base._record_provider_failure("dead", connection_error=True)
        ai_base._record_provider_failure("dead", connection_error=True)
        self.assertTrue(ai_base._is_circuit_open("dead"))

        with mock.patch.object(ai_queries, "get_configured_providers", return_value=[_P()]), \
             mock.patch.object(ai_queries, "_sync_db_to_env"):
            # ask_with_fallback must NOT attempt _ask_single on 'dead' —
            # it's filtered, and with no remaining providers it raises
            # the no-providers error rather than hanging on a dead host.
            with self.assertRaises(RuntimeError) as ctx:
                ai_queries.ask_with_fallback("ping")
            self.assertIn("No AI providers", str(ctx.exception))


class BoundedTestPromptTests(TestCase):
    """The /ai/test/ view must never hang: the Senate chain runs in a
    worker thread with a hard deadline and returns 504 on timeout."""

    def test_timeout_returns_504(self):
        from django.contrib.auth import get_user_model
        from django.test import Client
        from rest_framework.authtoken.models import Token
        import threading
        import time

        User = get_user_model()
        u = User.objects.create_user(username="tt-user", password="x")
        tok, _ = Token.objects.get_or_create(user=u)
        client = Client(SERVER_NAME="grid.smsly.cloud")

        class _SlowProvider:
            id = "slow"
            @staticmethod
            def name():
                return "slow"
            @staticmethod
            def is_configured():
                return True
            @staticmethod
            def ask(prompt, system_prompt=None):
                time.sleep(30)  # simulates a stalled provider
                return "late", "slow"

        started = threading.Event()

        def _slow_ask(prompt, system_prompt=None):
            started.set()
            time.sleep(30)
            return "late", "slow"

        with mock.patch.object(ai_queries, "get_configured_providers", return_value=[_SlowProvider()]), \
             mock.patch.object(ai_queries, "_sync_db_to_env"), \
             mock.patch.object(ai_queries, "_cached_ask", side_effect=lambda *a, **k: _slow_ask(*a)), \
             mock.patch.dict("os.environ", {"AI_TEST_TIMEOUT_SECONDS": "2"}):
            resp = client.post(
                "/api/v1/ai/test/",
                data={"prompt": "ping"},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Token {tok.key}",
            )
        self.assertEqual(resp.status_code, 504)
        self.assertEqual(resp.json().get("code"), "ai_test_timeout")
