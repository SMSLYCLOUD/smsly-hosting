"""
Regression tests for Finding #189 (terminal input rate limit).

``TerminalConsumer.receive`` must drop input frames that arrive
faster than the configured per-frame window. Without this, a
client can spam the container's exec stdin and amplify pressure
on the Docker daemon.

The test uses the async receive() coroutine directly and asserts:

  * the first frame is accepted (last_input_time is updated);
  * the second frame within the window is dropped silently;
  * after waiting past the window, the next frame is accepted.
"""
import asyncio
import inspect

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.consumers import TerminalConsumer


User = get_user_model()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class Finding189InputRateLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="ws-rate-189", password="p",
        )
        self.consumer = TerminalConsumer()
        self.consumer.user = self.user
        self.consumer._last_input_time = 0.0
        self.consumer._input_frame_window = 0.05

    def test_constructor_initialises_rate_limit_state(self):
        consumer = TerminalConsumer()
        self.assertTrue(hasattr(consumer, "_last_input_time"))
        self.assertTrue(hasattr(consumer, "_input_frame_window"))
        self.assertEqual(consumer._last_input_time, 0.0)
        self.assertGreater(consumer._input_frame_window, 0.0)

    def test_receive_drops_frame_within_rate_window(self):
        from unittest.mock import patch
        with patch.object(self.consumer, "_send_to_shell") as send:
            _run(self.consumer.receive(text_data='{"type":"input","payload":"YQ=="}'))
            _run(self.consumer.receive(text_data='{"type":"input","payload":"Yg=="}'))
        self.assertEqual(send.call_count, 1)

    def test_receive_accepts_frame_after_rate_window(self):
        from unittest.mock import patch
        with patch.object(self.consumer, "_send_to_shell") as send:
            _run(self.consumer.receive(text_data='{"type":"input","payload":"YQ=="}'))
            self.consumer._last_input_time -= 1.0
            _run(self.consumer.receive(text_data='{"type":"input","payload":"Yg=="}'))
        self.assertEqual(send.call_count, 2)

    def test_receive_source_contains_rate_limit_guard(self):
        source = inspect.getsource(TerminalConsumer.receive)
        self.assertIn("_input_frame_window", source)
        self.assertIn("_last_input_time", source)
