"""Unit tests for ecosystem task normalization helpers."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.deployments.tasks_ecosystem import (
    _normalize_env_vars,
    _resolve_env_placeholders,
    _runtime_watch_defaults,
)


class TasksEcosystemHelpersTests(SimpleTestCase):
    def test_normalize_env_vars_accepts_list_shape(self):
        raw = [
            {"key": "PORT", "default": "3000"},
            {"key": "SECRET_KEY", "is_secret": True},
            {"key": "OPENAI_API_KEY", "is_secret": True},
        ]

        out = _normalize_env_vars(raw)

        self.assertEqual(out["PORT"], "3000")
        self.assertEqual(out["SECRET_KEY"], "{{GENERATE}}")
        self.assertEqual(out["OPENAI_API_KEY"], "")

    def test_resolve_env_placeholders_handles_service_references(self):
        env_map = {
            "API_URL": "{{SERVICE:backend}}",
            "DATABASE_URL": "{{POSTGRES_URL}}",
            "JWT_SECRET": "{{GENERATE}}",
        }
        created = {"backend": SimpleNamespace(name="backend-api", internal_port=8000)}

        out = _resolve_env_placeholders(env_map, created)

        self.assertEqual(out["API_URL"], "http://backend-api:8000")
        self.assertTrue(out["DATABASE_URL"].startswith("postgresql://"))
        self.assertTrue(out["JWT_SECRET"])
        self.assertNotEqual(out["JWT_SECRET"], "{{GENERATE}}")

    def test_runtime_watch_defaults_prefill_email(self):
        user = SimpleNamespace(email="alerts@example.com")
        out = _runtime_watch_defaults(user)

        self.assertEqual(out["JULES_RUNTIME_WATCH"], "true")
        self.assertEqual(out["ALERT_EMAIL"], "alerts@example.com")
