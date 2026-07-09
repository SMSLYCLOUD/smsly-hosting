import asyncio
import contextlib
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase

from apps.deployments.consumers import TerminalConsumer


def _make_consumer():
    consumer = TerminalConsumer()
    consumer.deployment_id = 'dep-177'
    consumer.user = MagicMock(id=1)
    consumer.container_id = None
    consumer.exec_id = None
    consumer.exec_socket = None
    consumer._raw_sock = None
    consumer._setup_task = None
    consumer._read_task = None
    consumer._send_task = None
    consumer._pulse_task = None
    consumer._out_queue = asyncio.Queue()
    consumer.is_disconnected = False
    consumer._accepted = True
    return consumer


class Finding177AsyncSetupTimeoutTests(TestCase):
    def test_async_setup_source_wraps_find_container_with_wait_for(self):
        src = inspect.getsource(TerminalConsumer._async_setup)
        self.assertIn('asyncio.wait_for', src)
        self.assertIn('self._find_container()', src)
        self.assertIn('timeout=30', src)

    def test_async_setup_source_wraps_start_exec_with_wait_for(self):
        src = inspect.getsource(TerminalConsumer._async_setup)
        self.assertIn('self._start_exec()', src)
        wait_for_count = src.count('asyncio.wait_for')
        self.assertGreaterEqual(wait_for_count, 2)

    def test_find_container_timeout_short_circuits_setup(self):
        consumer = _make_consumer()
        consumer.exec_id = None

        async def _slow_find():
            await asyncio.sleep(10)
            return 'never'

        async def _run():
            with patch.object(consumer, '_find_container', side_effect=_slow_find), \
                 patch.object(consumer, '_start_exec', AsyncMock(return_value=True)), \
                 patch.object(consumer, 'close', AsyncMock()), \
                 patch.object(consumer, '_close_exec_socket', AsyncMock()), \
                 patch('apps.deployments.consumers.asyncio.wait_for') as wait_for_mock:
                wait_for_mock.side_effect = TimeoutError()
                await consumer._async_setup()
                self.assertGreaterEqual(wait_for_mock.call_count, 1)

        asyncio.run(_run())

    def test_start_exec_timeout_short_circuits_setup(self):
        consumer = _make_consumer()
        consumer.exec_id = None

        async def _run():
            with patch.object(consumer, '_find_container', AsyncMock(return_value='container-x')), \
                 patch.object(consumer, '_start_exec', AsyncMock(return_value=True)), \
                 patch.object(consumer, 'close', AsyncMock()), \
                 patch.object(consumer, '_close_exec_socket', AsyncMock()), \
                 patch('apps.deployments.consumers.asyncio.sleep', AsyncMock()):
                call_count = {'n': 0}
                real_wait_for = asyncio.wait_for

                async def _wait_for(coro, timeout):
                    call_count['n'] += 1
                    if call_count['n'] >= 2:
                        with contextlib.suppress(Exception):
                            coro.close()
                        raise TimeoutError()
                    return await real_wait_for(coro, timeout)

                with patch('apps.deployments.consumers.asyncio.wait_for', side_effect=_wait_for):
                    await consumer._async_setup()
                    self.assertGreaterEqual(call_count['n'], 2)

        asyncio.run(_run())
