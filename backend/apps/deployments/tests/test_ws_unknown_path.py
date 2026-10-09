"""Unknown WebSocket paths must close cleanly (4404), never 500."""
import asyncio

from channels.routing import URLRouter
from django.test import SimpleTestCase, override_settings

from apps.deployments.routing import UnknownWsConsumer, websocket_urlpatterns


CHANNEL_LAYERS_TEST = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


def _run(coro):
    return asyncio.run(coro)


@override_settings(CHANNEL_LAYERS=CHANNEL_LAYERS_TEST)
class UnknownWsPathTests(SimpleTestCase):
    def test_consumer_closes_4404(self):
        from channels.testing import WebsocketCommunicator

        async def scenario():
            comm = WebsocketCommunicator(
                UnknownWsConsumer.as_asgi(), "/ws/nope-not-real/")
            try:
                await comm.send_input({"type": "websocket.connect"})
                return await comm.receive_output(timeout=5)
            finally:
                await comm.disconnect()

        msg = _run(scenario())
        self.assertEqual(msg["type"], "websocket.close")
        self.assertEqual(msg.get("code"), 4404)

    def test_router_sends_unknown_path_to_catchall(self):
        """Full URLRouter: an unmatched path resolves without ValueError
        and ends in a clean 4404 close."""
        router = URLRouter(websocket_urlpatterns)
        scope = {
            "type": "websocket",
            "path": "/ws/definitely-unknown-xyz/",
            "headers": [],
            "query_string": b"",
            "subprotocols": [],
        }
        sent = []
        calls = 0

        async def receive():
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"type": "websocket.connect"}
            # Block: the app must NOT ask for more input after closing.
            # (close() sends the frame and returns; only the ASGI
            # server ends the scope in production.)
            await asyncio.Future()
            return {"type": "websocket.disconnect", "code": 1000}

        async def send(msg):
            sent.append(msg)

        async def run():
            task = asyncio.ensure_future(router(scope, receive, send))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if any(m["type"] == "websocket.close" for m in sent):
                    break
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            except Exception as exc:  # channels StopConsumer
                assert type(exc).__name__ == "StopConsumer", exc

        _run(run())
        closes = [m for m in sent if m["type"] == "websocket.close"]
        self.assertTrue(closes, sent)
        self.assertEqual(closes[0].get("code"), 4404)

    def test_catchall_routes_registered_last(self):
        patterns = [str(p.pattern) for p in websocket_urlpatterns]
        self.assertTrue(any("addon-terminal" in p for p in patterns))
        last_three = patterns[-3:]
        self.assertTrue(all(".*" in p for p in last_three), last_three)
