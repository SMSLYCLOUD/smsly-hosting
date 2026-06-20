# pylint: disable=invalid-name
"""
Regression tests for env-default safety checks.

Covers:
  1. GATEWAY_SECRET must be a distinct value from SECRET_KEY
     in production (not DEBUG, not IS_TESTING).
  2. RABBITMQ_PASSWORD must be set in production; the
     well-known placeholder ``smsly_password`` is rejected.
  3. REDIS_PASSWORD must be set in production; an empty value
     (no --requirepass on the broker) is rejected.
  4. In tests / DEBUG, the legacy fallbacks continue to work
     so existing test suites do not break.
  5. docker-compose.prod.yml enforces the same constraints
     via ${VAR:?error} bash parameter expansion.
"""
import os
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
COMPOSE_PATH = os.path.join(REPO_ROOT, "docker-compose.prod.yml")


class GatewaySecretDefaultsTests(SimpleTestCase):
    """GATEWAY_SECRET is the HMAC shared secret used for
    inter-node token exchange. It must be a separate value
    from SECRET_KEY in production.
    """

    def _resolve(self, *, debug, testing, raw_gateway):
        """Re-evaluate the resolver with the given flags."""
        from config import settings
        # Temporarily patch the module-level state.
        original_secret = settings._GATEWAY_SECRET_RAW
        original_debug = settings.DEBUG
        original_testing = settings.IS_TESTING
        try:
            settings._GATEWAY_SECRET_RAW = raw_gateway
            settings.DEBUG = debug
            settings.IS_TESTING = testing
            return settings._resolve_gateway_secret()
        finally:
            settings._GATEWAY_SECRET_RAW = original_secret
            settings.DEBUG = original_debug
            settings.IS_TESTING = original_testing

    def test_gateway_secret_required_in_production(self):
        with self.assertRaises(ImproperlyConfigured) as cm:
            self._resolve(
                debug=False, testing=False, raw_gateway=""
            )
        self.assertIn("GATEWAY_SECRET", str(cm.exception))

    def test_gateway_secret_falls_back_to_secret_key_in_debug(self):
        from config import settings
        result = self._resolve(
            debug=True, testing=False, raw_gateway=""
        )
        self.assertEqual(result, settings.SECRET_KEY)

    def test_gateway_secret_falls_back_in_testing(self):
        from config import settings
        result = self._resolve(
            debug=False, testing=True, raw_gateway=""
        )
        self.assertEqual(result, settings.SECRET_KEY)

    def test_gateway_secret_used_when_set(self):
        result = self._resolve(
            debug=False, testing=False, raw_gateway="my-distinct-secret"
        )
        self.assertEqual(result, "my-distinct-secret")


class RabbitMQPasswordDefaultsTests(SimpleTestCase):
    """RABBITMQ_PASSWORD must be set in production; the
    well-known placeholder ``smsly_password`` is rejected.
    """

    def _resolve(self, *, debug, testing, raw_password):
        from config import settings
        # decouple.config reads from .env files; mock it to
        # return our test value.
        def fake_config(name, default=None, **kwargs):
            if name == "RABBITMQ_PASSWORD":
                return raw_password
            return default
        with mock.patch("config.settings.config", side_effect=fake_config):
            original_debug = settings.DEBUG
            original_testing = settings.IS_TESTING
            try:
                settings.DEBUG = debug
                settings.IS_TESTING = testing
                return settings._resolve_rabbitmq_password()
            finally:
                settings.DEBUG = original_debug
                settings.IS_TESTING = original_testing

    def test_empty_password_rejected_in_production(self):
        with self.assertRaises(ImproperlyConfigured) as cm:
            self._resolve(debug=False, testing=False, raw_password="")
        self.assertIn("RABBITMQ_PASSWORD", str(cm.exception))

    def test_placeholder_password_rejected_in_production(self):
        with self.assertRaises(ImproperlyConfigured) as cm:
            self._resolve(
                debug=False, testing=False, raw_password="smsly_password"
            )
        self.assertIn("smsly_password", str(cm.exception))

    def test_placeholder_password_allowed_in_debug(self):
        result = self._resolve(
            debug=True, testing=False, raw_password="smsly_password"
        )
        self.assertEqual(result, "smsly_password")

    def test_empty_password_falls_back_in_testing(self):
        result = self._resolve(debug=False, testing=True, raw_password="")
        self.assertEqual(result, "test-rabbitmq-password")

    def test_real_password_accepted_in_production(self):
        result = self._resolve(
            debug=False, testing=False,
            raw_password="a" * 32,
        )
        self.assertEqual(result, "a" * 32)


class RedisPasswordDefaultsTests(SimpleTestCase):
    """REDIS_PASSWORD must be set in production; an empty
    value is rejected.
    """

    def _resolve(self, *, debug, testing, raw_password):
        from config import settings
        def fake_config(name, default=None, **kwargs):
            if name == "REDIS_PASSWORD":
                return raw_password
            return default
        with mock.patch("config.settings.config", side_effect=fake_config):
            original_debug = settings.DEBUG
            original_testing = settings.IS_TESTING
            try:
                settings.DEBUG = debug
                settings.IS_TESTING = testing
                return settings._resolve_redis_password()
            finally:
                settings.DEBUG = original_debug
                settings.IS_TESTING = original_testing

    def test_empty_password_rejected_in_production(self):
        with self.assertRaises(ImproperlyConfigured) as cm:
            self._resolve(debug=False, testing=False, raw_password="")
        self.assertIn("REDIS_PASSWORD", str(cm.exception))

    def test_empty_password_allowed_in_debug(self):
        result = self._resolve(debug=True, testing=False, raw_password="")
        self.assertEqual(result, "")

    def test_empty_password_allowed_in_testing(self):
        result = self._resolve(debug=False, testing=True, raw_password="")
        self.assertEqual(result, "")

    def test_real_password_accepted_in_production(self):
        result = self._resolve(
            debug=False, testing=False, raw_password="b" * 32
        )
        self.assertEqual(result, "b" * 32)


class FallbackBehaviorTests(SimpleTestCase):
    """In tests and DEBUG mode, the legacy fallbacks must
    still work so existing test suites and dev workflows
    don't break.
    """

    def test_settings_module_loaded(self):
        from config import settings
        self.assertTrue(hasattr(settings, "REDIS_PASSWORD"))
        self.assertTrue(hasattr(settings, "_RABBITMQ_PASS"))
        self.assertTrue(hasattr(settings, "GATEWAY_SECRET"))


class DockerfileComposeAlignmentTests(SimpleTestCase):
    """The platform refuses to boot if REDIS_PASSWORD /
    RABBITMQ_PASSWORD is unset in production. The
    docker-compose.prod.yml must enforce the same constraint
    via ${VAR:?error} bash parameter expansion so the broker
    never starts without --requirepass.
    """

    def _read_compose(self):
        with open(COMPOSE_PATH, encoding="utf-8") as fh:
            return fh.read()

    def test_docker_compose_redis_requires_password(self):
        content = self._read_compose()
        self.assertIn(
            "${REDIS_PASSWORD:?REDIS_PASSWORD must be set in .env}",
            content,
            "docker-compose.prod.yml must enforce REDIS_PASSWORD via "
            "${VAR:?...} so the broker cannot start without --requirepass.",
        )

    def test_docker_compose_rabbitmq_requires_password(self):
        content = self._read_compose()
        self.assertIn(
            "${RABBITMQ_PASSWORD:?RABBITMQ_PASSWORD must be set in .env}",
            content,
        )

    def test_docker_compose_rabbitmq_user_is_parameterized(self):
        content = self._read_compose()
        self.assertNotIn(
            "      RABBITMQ_DEFAULT_USER: smsly_user\n",
            content,
            "RABBITMQ_DEFAULT_USER must be parameterized via "
            "${RABBITMQ_DEFAULT_USER:-...} so operators can change it.",
        )
        self.assertIn(
            "${RABBITMQ_DEFAULT_USER:-smsly_user}",
            content,
        )

