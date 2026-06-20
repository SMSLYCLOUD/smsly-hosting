# pylint: disable=invalid-name
"""Regression tests for Issues 55 and 59 (consumer disconnect / cleanup).

The TerminalConsumer had two related gaps:

* On ``disconnect()`` the background ``_setup_task`` was cancelled but
  not awaited. The task could keep the exec attach running on the
  Docker daemon even after the WebSocket was gone.
* ``_close_exec_socket()`` was only called from ``disconnect()`` and
  only synchronously. If the setup task errored before the disconnect
  ran, the exec socket was leaked.

The fix:
* ``disconnect()`` now ``await``s the cancelled task (with a short
  timeout) and then awaits ``_close_exec_socket()``.
* ``_async_setup()`` is wrapped in ``try / finally`` that always calls
  ``_close_exec_socket()`` so the socket is closed on success, error,
  and cancellation paths alike.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase

from apps.deployments.consumers import TerminalConsumer


def _make_consumer():
    consumer = TerminalConsumer()
    consumer.deployment_id = 'dep-1'
    consumer.user = MagicMock(id=1)
    consumer.container_id = 'container-1'
    consumer.exec_id = 'exec-1'
    consumer.exec_socket = MagicMock()
    consumer._raw_sock = MagicMock()
    consumer._setup_task = None
    consumer._read_task = None
    consumer._send_task = None
    consumer._pulse_task = None
    consumer._out_queue = asyncio.Queue()
    consumer.is_disconnected = False
    consumer._accepted = True
    return consumer


class TerminalConsumerDisconnectCleanupTests(TestCase):
    def test_disconnect_awaits_setup_task(self):
        """If the setup task is still running when the client disconnects,
        it must be cancelled AND awaited (with a short timeout) so it has
        a chance to release the exec attach before disconnect returns."""
        consumer = _make_consumer()

        async def _slow_setup():
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise

        loop = asyncio.new_event_loop()
        try:
            setup_task = loop.create_task(_slow_setup())
            consumer._setup_task = setup_task
            # give the task a moment to start
            loop.run_until_complete(asyncio.sleep(0))
            consumer._read_task = None
            consumer._send_task = None
            consumer._pulse_task = None

            close_mock = AsyncMock()
            with patch.object(consumer, '_close_exec_socket', close_mock):
                loop.run_until_complete(consumer.disconnect(code=1000))

            self.assertTrue(setup_task.done())
        finally:
            loop.close()

    def test_disconnect_closes_exec_socket(self):
        consumer = _make_consumer()
        consumer._setup_task = None
        consumer._read_task = None
        consumer._send_task = None
        consumer._pulse_task = None

        close_mock = AsyncMock()
        with patch.object(consumer, '_close_exec_socket', close_mock):
            asyncio.run(consumer.disconnect(code=1000))
        close_mock.assert_awaited()

    def test_close_exec_socket_is_async(self):
        """The new contract: _close_exec_socket is a coroutine."""
        consumer = _make_consumer()
        self.assertTrue(asyncio.iscoroutinefunction(consumer._close_exec_socket))

    def test_async_setup_closes_socket_in_finally(self):
        """On any path out of _async_setup, the exec socket must be closed
        (success, error, or cancellation)."""
        consumer = _make_consumer()

        async def _noop():
            return None

        with patch.object(consumer, '_find_container', new=AsyncMock(return_value=None)), \
             patch.object(consumer, '_out_queue'), \
             patch.object(consumer, '_close_exec_socket', new=AsyncMock()) as close_mock, \
             patch.object(consumer, '_async_setup', new=_noop):
            # The actual setup body returns early when _find_container is None.
            # We patch _async_setup entirely to bypass, then verify the wrapper.
            pass

        # Re-test the real flow: the production code wraps the body in
        # try / finally, so when exec_id is set the finally block runs
        # _close_exec_socket even on early return / error.
        async def _run():
            consumer.exec_id = 'exec-1'
            with patch.object(consumer, '_find_container', new=AsyncMock(return_value=None)):
                consumer._out_queue = asyncio.Queue()
                close_mock2 = AsyncMock()
                with patch.object(consumer, '_close_exec_socket', new=close_mock2):
                    await consumer._async_setup()
            return close_mock2

        close_mock = asyncio.run(_run())
        close_mock.assert_awaited()
