# pylint: disable=invalid-name
"""Stability contract for the interactive service console.

The console died too easily:
- a single quiet 20s window killed the read loop (TimeoutError → return),
  dropping live shells while the user was thinking;
- exec reconnects hammered the Docker API with a fixed 1s sleep;
- audit logging ran inline, so a slow DB stalled keystrokes;
- missing-user close only fired under DEBUG;
- terminal resizes never reached the remote pty (80-col wrap corruption).
"""
import asyncio
import json
from unittest import IsolatedAsyncioTestCase, mock

from apps.deployments.consumers.terminal import (
    TerminalConsumer,
    _swallow_audit_error,
)


def _consumer():
    consumer = TerminalConsumer()
    consumer.user = mock.MagicMock()
    return consumer


class ReceiveTests(IsolatedAsyncioTestCase):
    async def test_ping_refreshes_activity_and_pongs(self):
        consumer = _consumer()
        before = consumer._last_activity
        await consumer.receive(text_data=json.dumps({"type": "ping"}))
        self.assertGreaterEqual(consumer._last_activity, before)
        self.assertEqual(
            consumer._out_queue.get_nowait(), {"type": "pong"})

    async def test_resize_is_routed(self):
        consumer = _consumer()
        with mock.patch.object(
            consumer, "_handle_resize", new_callable=mock.AsyncMock,
        ) as handle:
            await consumer.receive(
                text_data=json.dumps(
                    {"type": "resize", "cols": 120, "rows": 30}))
        handle.assert_called_once()

    async def test_missing_user_closes_4001(self):
        consumer = TerminalConsumer()
        consumer.user = None
        with mock.patch.object(
            consumer, "close", new_callable=mock.AsyncMock,
        ) as close:
            await consumer.receive(text_data="x")
        close.assert_called_once_with(code=4001)


class ResizeTests(IsolatedAsyncioTestCase):
    async def test_valid_resize_calls_exec_resize(self):
        consumer = _consumer()
        consumer.exec_id = "abc123"
        with mock.patch(
            "apps.cloud.docker_client.get_docker_exec_client",
        ) as get_client:
            await consumer._handle_resize(
                {"type": "resize", "cols": 120, "rows": 30})
        get_client.return_value.api.exec_resize.assert_called_once_with(
            "abc123", 30, 120)

    async def test_absurd_resize_ignored(self):
        consumer = _consumer()
        consumer.exec_id = "abc123"
        with mock.patch(
            "apps.cloud.docker_client.get_docker_exec_client",
        ) as get_client:
            await consumer._handle_resize({"cols": 5, "rows": 2})
            await consumer._handle_resize({"cols": "wide"})
        get_client.assert_not_called()

    async def test_resize_without_exec_id_ignored(self):
        consumer = _consumer()
        consumer.exec_id = None
        with mock.patch(
            "apps.cloud.docker_client.get_docker_exec_client",
        ) as get_client:
            await consumer._handle_resize({"cols": 120, "rows": 30})
        get_client.assert_not_called()


class AuditTests(IsolatedAsyncioTestCase):
    async def test_audit_failure_never_raises(self):
        consumer = _consumer()
        with mock.patch(
            "apps.deployments.utils.log_event",
            side_effect=RuntimeError("db down"),
        ):
            await consumer._audit_command("ls -la")

    def test_swallow_audit_error(self):
        task = mock.MagicMock()
        task.exception.side_effect = RuntimeError("boom")
        _swallow_audit_error(task)
        cancelled = mock.MagicMock()
        cancelled.exception.side_effect = asyncio.CancelledError()
        _swallow_audit_error(cancelled)


class ReadLoopTests(IsolatedAsyncioTestCase):
    async def test_transient_read_timeout_continues_loop(self):
        """A quiet shell must not end the session: the first
        wait_for timeout continues the loop (previously: return)."""
        consumer = _consumer()
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise asyncio.TimeoutError()
            raise asyncio.CancelledError()

        consumer._blocking_read = flaky
        await consumer._read_output()
        self.assertEqual(len(calls), 2)
