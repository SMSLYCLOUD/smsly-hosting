import inspect

from django.test import SimpleTestCase, override_settings

from apps.deployments import consumers


class Finding91WsIdleTimeoutConfigurableTests(SimpleTestCase):
    def test_settings_attribute_is_referenced_in_source(self):
        source = inspect.getsource(consumers)
        self.assertIn("WEBSOCKET_IDLE_TIMEOUT", source)

    def test_default_value_is_420_seconds(self):
        source = inspect.getsource(consumers)
        self.assertIn(
            'getattr(settings, "WEBSOCKET_IDLE_TIMEOUT", 420)',
            source,
        )

    @override_settings(WEBSOCKET_IDLE_TIMEOUT=120)
    def test_resolve_returns_configured_value(self):
        from django.conf import settings
        resolved = float(
            getattr(settings, "WEBSOCKET_IDLE_TIMEOUT", 420)
        )
        self.assertEqual(resolved, 120.0)

    @override_settings(WEBSOCKET_IDLE_TIMEOUT="180")
    def test_string_setting_is_cast_to_float(self):
        from django.conf import settings
        resolved = float(
            getattr(settings, "WEBSOCKET_IDLE_TIMEOUT", 420)
        )
        self.assertEqual(resolved, 180.0)
