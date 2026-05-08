"""Unit tests for ecosystem task normalization helpers."""

from types import SimpleNamespace
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.deployments.tasks_ecosystem import (
    _apply_service_profile,
    _normalize_env_vars,
    _resolve_env_placeholders,
    _runtime_watch_defaults,
    _select_shared_addon_anchor,
    ecosystem_scan_task,
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

    def test_apply_service_profile_prefers_plan_default_branch(self):
        service = SimpleNamespace(
            repository_url="",
            branch="",
            internal_port=0,
            buildpack="NIXPACKS",
            deploy_mode="SINGLE",
            compose_file="",
            compose_main_service="",
            root_directory="/",
            provider=None,
            health_check_path="/health",
            save=lambda *args, **kwargs: None,
        )
        plan = {
            "repo": "owner/repo",
            "default_branch": "master",
            "build": "nixpacks",
        }

        _apply_service_profile(service, plan, provider=None, port=8080)

        self.assertEqual(service.repository_url, "https://github.com/owner/repo")
        self.assertEqual(service.branch, "master")
        self.assertEqual(service.internal_port, 8080)

    def test_select_shared_addon_anchor_prefers_smsly_core(self):
        services = [
            SimpleNamespace(name="smsly-voice", repository_url="https://github.com/acme/smsly-voice"),
            SimpleNamespace(name="smsly-core", repository_url="https://github.com/acme/smsly-core"),
            SimpleNamespace(name="smsly-marketing", repository_url="https://github.com/acme/smsly-marketing"),
        ]

        anchor = _select_shared_addon_anchor(services)

        self.assertIsNotNone(anchor)
        self.assertEqual(anchor.name, "smsly-core")

    def test_select_shared_addon_anchor_falls_back_to_first(self):
        services = [
            SimpleNamespace(name="payments-api", repository_url="https://github.com/acme/payments-api"),
            SimpleNamespace(name="web-frontend", repository_url="https://github.com/acme/web-frontend"),
        ]

        anchor = _select_shared_addon_anchor(services)

        self.assertIsNotNone(anchor)
        self.assertEqual(anchor.name, "payments-api")


class EcosystemScanTaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ecosystem-scan",
            email="ecosystem-scan@example.com",
            password="password123",
        )

    @patch("services.ecosystem.scan_and_analyze", side_effect=SoftTimeLimitExceeded())
    @patch("apps.deployments.views_github._get_github_token", return_value="gh-token")
    def test_scan_timeout_returns_actionable_error(self, _token_mock, _scan_mock):
        result = ecosystem_scan_task.run(str(self.user.id), 30)

        self.assertEqual(result["code"], "ecosystem_scan_timeout")
        self.assertTrue(result["retryable"])
        self.assertIn("timed out", result["error"])
