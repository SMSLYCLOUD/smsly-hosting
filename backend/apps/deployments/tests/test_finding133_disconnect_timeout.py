import asyncio
import contextlib
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase

from apps.deployments.consumers import TerminalConsumer


def _make_consumer():
    consumer = TerminalConsumer()
    consumer.deployment_id = 'dep-133'
    consumer.user = MagicMock(id=1)
    consumer.container_id = 'container-133'
    consumer.exec_id = 'exec-133'
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


class Finding133DisconnectTimeoutTests(TestCase):
    def test_disconnect_source_uses_two_second_timeout(self):
        src = inspect.getsource(TerminalConsumer.disconnect)
        self.assertIn('timeout=2.0', src)
        self.assertNotIn('timeout=0.2', src)

    def test_disconnect_source_wraps_close_exec_socket_in_try_finally(self):
        src = inspect.getsource(TerminalConsumer.disconnect)
        self.assertIn('try:', src)
        self.assertIn('finally:', src)
        self.assertIn('_close_exec_socket()', src)

    def test_disconnect_runs_close_exec_socket_even_if_cancel_step_raises(self):
        consumer = _make_consumer()

        bad_task = MagicMock()
        bad_task.done.return_value = False
        bad_task.cancel.side_effect = RuntimeError('boom on cancel')
        consumer._setup_task = bad_task

        close_mock = AsyncMock()

        async def _run():
            with patch.object(consumer, '_close_exec_socket', close_mock):
                with contextlib.suppress(RuntimeError):
                    await consumer.disconnect(code=1000)

        asyncio.run(_run())
        close_mock.assert_awaited()

    def test_disconnect_waits_up_to_two_seconds_for_slow_task(self):
        consumer = _make_consumer()

        async def _slow_setup():
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise

        loop = asyncio.new_event_loop()
        try:
            setup_task = loop.create_task(_slow_setup())
            consumer._setup_task = setup_task
            loop.run_until_complete(asyncio.sleep(0))
            consumer._read_task = None
            consumer._send_task = None
            consumer._pulse_task = None

            close_mock = AsyncMock()
            with patch.object(consumer, '_close_exec_socket', close_mock):
                loop.run_until_complete(consumer.disconnect(code=1000))

            self.assertTrue(setup_task.done())
            close_mock.assert_awaited()
        finally:
            loop.close()
