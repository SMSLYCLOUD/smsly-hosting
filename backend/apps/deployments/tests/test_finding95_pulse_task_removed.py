import contextlib
import inspect

from django.test import SimpleTestCase

from apps.deployments.consumers import TerminalConsumer


class Finding95PulseTaskRemovalTests(SimpleTestCase):

    def test_pulse_task_init_removed_from_constructor(self):
        init_src = inspect.getsource(TerminalConsumer.__init__)
        self.assertNotIn('_pulse_task', init_src)

    def test_pulse_task_not_in_disconnect(self):
        disconnect_src = inspect.getsource(TerminalConsumer.disconnect)
        self.assertNotIn('_pulse_task', disconnect_src)

    def test_consumer_instance_does_not_have_pulse_task_attr_after_init(self):
        consumer = TerminalConsumer()
        try:
            self.assertFalse(
                hasattr(consumer, '_pulse_task'),
                'TerminalConsumer instances should not carry a '
                '_pulse_task attribute; the dead field has been removed.',
            )
        finally:
            for sock_name in ('_raw_sock', 'exec_socket'):
                sock = getattr(consumer, sock_name, None)
                if sock is not None and hasattr(sock, 'close'):
                    with contextlib.suppress(Exception):
                        sock.close()
