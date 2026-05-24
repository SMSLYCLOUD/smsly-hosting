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
    ecosystem_deploy_task,
    ecosystem_scan_task,
)
from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, EnvironmentVariable, ManagedServer, Service


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

        out = _resolve_env_placeholders(
            env_map,
            created,
            shared_addons={"POSTGRES": "postgresql://smsly:smsly@postgres:5432/smsly"},
        )

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

    def test_apply_service_profile_accepts_full_github_url(self):
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

        _apply_service_profile(
            service,
            {"repo": "https://github.com/owner/repo.git", "branch": "main"},
            provider=None,
            port=3000,
        )

        self.assertEqual(service.repository_url, "https://github.com/owner/repo.git")

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


class EcosystemDeployTaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ecosystem-deploy",
            email="deploy@example.com",
            password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="Local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    @patch("apps.deployments.tasks_ecosystem._queue_wave", return_value=1)
    def test_local_provider_without_managed_server_queues_local_deployment(self, _queue_wave):
        plan = {
            "services": [
                {
                    "name": "api",
                    "repo": "owner/api",
                    "stack": "node",
                    "port": 3000,
                    "env_vars": {},
                }
            ]
        }

        with self.settings(SENATE_ENABLED=False):
            result = ecosystem_deploy_task.run(str(self.user.id), plan)

        self.assertEqual(result["failed"], 0)
        service = Service.objects.get(owner=self.user, name="api")
        deployment = Deployment.objects.get(service=service)
        self.assertIsNone(service.server)
        self.assertIsNone(deployment.target_server)
        self.assertTrue(deployment.target_is_local)
        self.assertEqual(deployment.branch, service.branch)
        self.assertEqual(service.repository_url, "https://github.com/owner/api")

    @patch("apps.deployments.tasks_ecosystem._queue_wave", return_value=1)
    def test_existing_service_is_reassigned_to_selected_node_and_deployment_targets_it(self, _queue_wave):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Worker",
            host="10.0.0.2",
            status=ManagedServer.Status.ONLINE,
            is_primary=False,
        )
        service = Service.objects.create(
            owner=self.user,
            name="api",
            provider=self.provider,
            repository_url="https://github.com/old/api",
            server=None,
        )
        plan = {
            "services": [
                {
                    "name": "api",
                    "repo": "owner/api",
                    "stack": "node",
                    "port": 3000,
                    "env_vars": {},
                }
            ]
        }

        with self.settings(SENATE_ENABLED=False):
            result = ecosystem_deploy_task.run(str(self.user.id), plan)

        self.assertEqual(result["failed"], 0)
        service.refresh_from_db()
        deployment = Deployment.objects.get(service=service)
        self.assertEqual(service.server, server)
        self.assertEqual(deployment.target_server, server)
        self.assertFalse(deployment.target_is_local)

    @patch("services.addon_provisioner.addon_provisioner.provision", return_value=("postgres-cid", "postgresql://u:p@db:5432/app"))
    @patch("apps.deployments.tasks_ecosystem._queue_wave", return_value=1)
    def test_top_level_addons_and_shared_secret_placeholders_are_resolved(self, _queue_wave, _provision):
        plan = {
            "addons": [{"type": "POSTGRES", "shared_by": ["api"]}],
            "services": [
                {
                    "name": "api",
                    "repo": "https://github.com/owner/api.git",
                    "stack": "django",
                    "port": 8000,
                    "env_vars": {
                        "DATABASE_URL": "{{POSTGRES_URL}}",
                        "JWT_SECRET": "{{SHARED_SECRET:jwt}}",
                    },
                }
            ],
        }

        with self.settings(SENATE_ENABLED=False):
            result = ecosystem_deploy_task.run(str(self.user.id), plan)

        self.assertEqual(result["failed"], 0)
        service = Service.objects.get(owner=self.user, name="api")
        env = {var.key: var.value for var in EnvironmentVariable.objects.filter(service=service)}
        self.assertEqual(service.repository_url, "https://github.com/owner/api")
        self.assertEqual(env["DATABASE_URL"], "postgresql://u:p@db:5432/app")
        self.assertEqual(env["POSTGRES_URL"], "postgresql://u:p@db:5432/app")
        self.assertTrue(env["JWT_SECRET"])
        self.assertNotIn("{{", env["JWT_SECRET"])
