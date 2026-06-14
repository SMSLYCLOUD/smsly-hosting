"""Tests for the WebSocket auth-token subprotocol pattern.

The TerminalConsumer must NEVER accept a DRF token from the URL query
string. Query strings are recorded in reverse-proxy access logs,
browser history, and the ``Referer`` header of cross-origin requests,
and a long-lived DRF token must never appear in a URL.

The token is read from a Sec-WebSocket-Protocol subprotocol instead.
This file exercises the TerminalConsumer.connect() flow against a
hand-crafted scope to verify both behaviours:

    1. Tokens offered in the URL query string are rejected.
    2. Tokens offered as a Sec-WebSocket-Protocol subprotocol are
       accepted (and the negotiated subprotocol is ``"token"``).
"""
import asyncio
from unittest.mock import AsyncMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token

from apps.deployments.consumers import TerminalConsumer


class TerminalConsumerTokenSafetyTests(TestCase):
    """Verify the TerminalConsumer does not trust query-string tokens."""

    def setUp(self):
        self.user = User.objects.create_user(username="wstest", password="x")
        self.token = Token.objects.create(user=self.user)

    def _make_consumer(self, *, query_string='', subprotocols=None):
        consumer = TerminalConsumer()
        consumer.scope = {
            'type': 'websocket',
            'path': '/ws/terminal/some-id/',
            'query_string': query_string.encode('utf-8') if isinstance(query_string, str) else query_string,
            'url_route': {'args': (), 'kwargs': {'deployment_id': 'some-id'}},
            'subprotocols': list(subprotocols or []),
        }
        return consumer

    def test_rejects_token_in_query_string(self):
        """A DRF token placed in the URL query string MUST be rejected.

        Browsers (and reverse proxies) record query strings in
        multiple places. The token must be read from the WS
        subprotocol header instead.
        """
        consumer = self._make_consumer(
            query_string=f'token={self.token.key}',
            subprotocols=[],
        )
        accept_mock = AsyncMock()
        close_mock = AsyncMock()
        send_mock = AsyncMock()
        with patch.object(consumer, 'accept', accept_mock), \
             patch.object(consumer, 'close', close_mock), \
             patch.object(consumer, 'send', send_mock):
            asyncio.run(consumer.connect())

        accept_mock.assert_not_called()
        self.assertTrue(close_mock.await_count >= 1)
        close_codes = [
            call.kwargs.get('code') if call.kwargs else (call.args[0] if call.args else None)
            for call in close_mock.await_args_list
        ]
        self.assertIn(4001, close_codes)

    def test_rejects_when_no_subprotocols_and_no_token(self):
        """No subprotocols at all → close(4001)."""
        consumer = self._make_consumer(
            query_string='',
            subprotocols=[],
        )
        accept_mock = AsyncMock()
        close_mock = AsyncMock()
        with patch.object(consumer, 'accept', accept_mock), \
             patch.object(consumer, 'close', close_mock):
            asyncio.run(consumer.connect())

        accept_mock.assert_not_called()
        self.assertTrue(close_mock.await_count >= 1)
        close_codes = [
            call.kwargs.get('code') if call.kwargs else (call.args[0] if call.args else None)
            for call in close_mock.await_args_list
        ]
        self.assertIn(4001, close_codes)

    def test_rejects_marker_only_subprotocol(self):
        """A subprotocol of just 'token' (no auth key) is rejected."""
        consumer = self._make_consumer(
            query_string='',
            subprotocols=['token'],
        )
        accept_mock = AsyncMock()
        close_mock = AsyncMock()
        with patch.object(consumer, 'accept', accept_mock), \
             patch.object(consumer, 'close', close_mock):
            asyncio.run(consumer.connect())

        accept_mock.assert_not_called()
        close_codes = [
            call.kwargs.get('code') if call.kwargs else (call.args[0] if call.args else None)
            for call in close_mock.await_args_list
        ]
        self.assertIn(4001, close_codes)

    def test_accepts_token_marker_pair_subprotocol(self):
        """Subprotocols ['token', '<key>'] are accepted and the marker
        is negotiated. The actual key is never echoed back."""
        consumer = self._make_consumer(
            query_string='',
            subprotocols=['token', self.token.key],
        )
        accept_mock = AsyncMock()
        close_mock = AsyncMock()
        with patch.object(consumer, 'accept', accept_mock), \
             patch.object(consumer, 'close', close_mock), \
             patch.object(consumer, '_authenticate_token', AsyncMock(return_value=self.user)), \
             patch.object(consumer, '_verify_ownership', AsyncMock(return_value=True)):
            asyncio.run(consumer.connect())

        accept_mock.assert_awaited_with(subprotocol='token')
        close_codes = [
            call.kwargs.get('code') if call.kwargs else (call.args[0] if call.args else None)
            for call in close_mock.await_args_list
        ]
        self.assertNotIn(4001, close_codes)
        self.assertNotIn(4002, close_codes)
        self.assertNotIn(4003, close_codes)

    def test_accepts_token_dot_key_subprotocol(self):
        """Subprotocols ['token.<key>'] are accepted."""
        consumer = self._make_consumer(
            query_string='',
            subprotocols=[f'token.{self.token.key}'],
        )
        accept_mock = AsyncMock()
        close_mock = AsyncMock()
        with patch.object(consumer, 'accept', accept_mock), \
             patch.object(consumer, 'close', close_mock), \
             patch.object(consumer, '_authenticate_token', AsyncMock(return_value=self.user)), \
             patch.object(consumer, '_verify_ownership', AsyncMock(return_value=True)):
            asyncio.run(consumer.connect())

        accept_mock.assert_awaited_with(subprotocol='token')
        close_codes = [
            call.kwargs.get('code') if call.kwargs else (call.args[0] if call.args else None)
            for call in close_mock.await_args_list
        ]
        self.assertNotIn(4001, close_codes)
        self.assertNotIn(4002, close_codes)
        self.assertNotIn(4003, close_codes)

    def test_accepts_legacy_single_token_subprotocol(self):
        """Legacy: a single subprotocol that IS the token is accepted."""
        consumer = self._make_consumer(
            query_string='',
            subprotocols=[self.token.key],
        )
        accept_mock = AsyncMock()
        close_mock = AsyncMock()
        with patch.object(consumer, 'accept', accept_mock), \
             patch.object(consumer, 'close', close_mock), \
             patch.object(consumer, '_authenticate_token', AsyncMock(return_value=self.user)), \
             patch.object(consumer, '_verify_ownership', AsyncMock(return_value=True)):
            asyncio.run(consumer.connect())

        accept_mock.assert_awaited_with(subprotocol='token')
        close_codes = [
            call.kwargs.get('code') if call.kwargs else (call.args[0] if call.args else None)
            for call in close_mock.await_args_list
        ]
        self.assertNotIn(4001, close_codes)
        self.assertNotIn(4002, close_codes)
        self.assertNotIn(4003, close_codes)

    def test_rejects_invalid_token(self):
        """A wrong key with a valid-looking subprotocol is closed(4002)."""
        consumer = self._make_consumer(
            query_string='',
            subprotocols=['token', 'not-a-real-token'],
        )
        accept_mock = AsyncMock()
        close_mock = AsyncMock()
        with patch.object(consumer, 'accept', accept_mock), \
             patch.object(consumer, 'close', close_mock), \
             patch.object(consumer, '_authenticate_token', AsyncMock(return_value=None)):
            asyncio.run(consumer.connect())

        accept_mock.assert_not_called()
        close_codes = [
            call.kwargs.get('code') if call.kwargs else (call.args[0] if call.args else None)
            for call in close_mock.await_args_list
        ]
        self.assertIn(4002, close_codes)
