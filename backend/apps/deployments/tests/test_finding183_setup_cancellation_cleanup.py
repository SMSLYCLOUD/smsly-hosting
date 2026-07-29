import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase

from apps.deployments.consumers import TerminalConsumer


def _make_consumer():
    consumer = TerminalConsumer()
    consumer.deployment_id = 'dep-183'
    consumer.user = MagicMock(id=1)
    consumer.container_id = 'container-183'
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


class Finding183AsyncSetupCancellationCleanupTests(TestCase):
    def test_async_setup_source_uses_try_finally_for_close_exec_socket(self):
        src = inspect.getsource(TerminalConsumer._async_setup)
        self.assertIn('finally:', src)
        self.assertIn('_close_exec_socket()', src)
        self.assertIn('CancelledError', src)
        self.assertIn('raise', src)

    def test_async_setup_reraises_cancelled_error(self):
        consumer = _make_consumer()
        consumer.exec_id = 'exec-183'

        async def _hang_find():
            await asyncio.sleep(10)
            return 'never'

        async def _run():
            close_mock = AsyncMock()
            with patch.object(consumer, '_find_container', side_effect=_hang_find), \
                 patch.object(consumer, '_close_exec_socket', close_mock):
                task = asyncio.create_task(consumer._async_setup())
                await asyncio.sleep(0.1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            return close_mock

        close_mock = asyncio.run(_run())
        close_mock.assert_awaited()

    def test_async_setup_closes_exec_socket_when_start_exec_throws(self):
        consumer = _make_consumer()
        consumer.exec_id = 'exec-183'

        async def _run():
            close_mock = AsyncMock()
            with patch.object(consumer, '_find_container', AsyncMock(return_value='container-x')), \
                 patch.object(consumer, '_start_exec', AsyncMock(side_effect=RuntimeError('start fail'))), \
                 patch.object(consumer, 'close', AsyncMock()), \
                 patch.object(consumer, '_close_exec_socket', close_mock), \
                 patch('apps.deployments.consumers.terminal.asyncio.sleep', AsyncMock()):
                await consumer._async_setup()
            return close_mock

        close_mock = asyncio.run(_run())
        close_mock.assert_awaited()

    def test_async_setup_skips_close_when_exec_id_never_set(self):
        consumer = _make_consumer()
        consumer.exec_id = None

        async def _run():
            close_mock = AsyncMock()
            with patch.object(consumer, '_find_container', AsyncMock(return_value=None)), \
                 patch.object(consumer, '_close_exec_socket', close_mock):
                await consumer._async_setup()
            return close_mock

        close_mock = asyncio.run(_run())
        close_mock.assert_not_called()
