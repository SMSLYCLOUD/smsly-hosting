import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase

from apps.deployments.consumers import TerminalConsumer


def _make_consumer():
    consumer = TerminalConsumer()
    consumer.deployment_id = 'dep-148'
    consumer.user = MagicMock(id=1)
    consumer.container_id = 'container-148'
    consumer.exec_id = 'exec-148'
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


class _FakeConnectionClosed(Exception):
    pass


class Finding148SendLoopConnectionClosedTests(TestCase):
    def test_send_loop_closes_on_connection_closed_message_send(self):
        consumer = _make_consumer()

        async def _run():
            await consumer._out_queue.put({'message': 'hello'})

            close_mock = AsyncMock()
            send_mock = AsyncMock(side_effect=_FakeConnectionClosed('peer gone'))

            with patch.object(consumer, 'send', send_mock), \
                 patch.object(consumer, 'close', close_mock):
                task = asyncio.create_task(consumer._send_loop())
                await asyncio.sleep(0.3)
                consumer.is_disconnected = True
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except TimeoutError:
                    task.cancel()
            return close_mock

        close_mock = asyncio.run(_run())
        close_mock.assert_awaited()

    def test_send_loop_reraises_cancelled_error(self):
        consumer = _make_consumer()

        async def _run():
            consumer._accepted = True
            send_mock = AsyncMock()
            with patch.object(consumer, 'send', send_mock), \
                 patch.object(consumer, 'close', AsyncMock()):
                task = asyncio.create_task(consumer._send_loop())
                await asyncio.sleep(0.1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(_run())

    def test_send_loop_closes_on_keepalive_connection_closed(self):
        consumer = _make_consumer()

        async def _run():
            close_mock = AsyncMock()
            send_mock = AsyncMock(side_effect=_FakeConnectionClosed('idle peer gone'))
            with patch.object(consumer, 'send', send_mock), \
                 patch.object(consumer, 'close', close_mock):
                task = asyncio.create_task(consumer._send_loop())
                try:
                    await asyncio.wait_for(task, timeout=6.0)
                except TimeoutError:
                    task.cancel()
            return close_mock

        close_mock = asyncio.run(_run())
        close_mock.assert_awaited()
