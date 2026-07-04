"""Unit tests for ecosystem task normalization helpers."""

from types import SimpleNamespace
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    ManagedServer,
    Service,
)
from apps.deployments.tasks_ecosystem import (
    _apply_service_profile,
    _build_dependency_waves,
    _normalize_buildpack,
    _normalize_env_vars,
    _placeholder_addon_types,
    _resolve_dependency_map,
    _resolve_env_placeholders,
    _runtime_watch_defaults,
    _select_shared_addon_anchor,
    _service_placeholder_target,
    _validate_resolved_env,
    ecosystem_deploy_task,
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


class EmbeddedPlaceholderResolutionTests(SimpleTestCase):
    """Tests for embedded placeholder resolution in env var values."""

    def test_embedded_postgres_url_with_db_suffix(self):
        """{{POSTGRES_URL}}/identity must resolve to postgres://.../identity."""
        env_map = {"DATABASE_URL": "{{POSTGRES_URL}}/identity"}
        out = _resolve_env_placeholders(
            env_map, {},
            shared_addons={"POSTGRES": "postgres://user:pass@db:5432/main"},
        )
        self.assertEqual(out["DATABASE_URL"], "postgres://user:pass@db:5432/identity")

    def test_embedded_service_reference_wss(self):
        """wss://{{SERVICE:smsly-security-gateway}} must resolve."""
        created = {
            "smsly-security-gateway": SimpleNamespace(
                name="smsly-security-gateway", internal_port=8000,
            ),
        }
        env_map = {"WS_URL": "wss://{{SERVICE:smsly-security-gateway}}"}
        out = _resolve_env_placeholders(env_map, created)
        self.assertEqual(out["WS_URL"], "wss://smsly-security-gateway:8000")

    def test_full_service_reference_includes_http_scheme(self):
        """{{SERVICE:api}} as a full value must resolve to an HTTP URL."""
        created = {
            "api": SimpleNamespace(name="api", internal_port=8080),
        }
        out = _resolve_env_placeholders({"API_URL": "{{SERVICE:api}}"}, created)
        self.assertEqual(out["API_URL"], "http://api:8080")

    def test_multiple_placeholders_in_one_string(self):
        """Multiple placeholders in one value must all resolve."""
        env_map = {
            "COMPOSITE": "{{POSTGRES_URL}}?redis={{REDIS_URL}}",
        }
        out = _resolve_env_placeholders(
            env_map, {},
            shared_addons={
                "POSTGRES": "postgres://u:p@db:5432/app",
                "REDIS": "redis://redis:6379/0",
            },
        )
        self.assertEqual(
            out["COMPOSITE"],
            "postgres://u:p@db:5432/app?redis=redis://redis:6379/0",
        )

    def test_plain_string_without_placeholders_unchanged(self):
        env_map = {"PORT": "3000", "NODE_ENV": "production"}
        out = _resolve_env_placeholders(env_map, {})
        self.assertEqual(out["PORT"], "3000")
        self.assertEqual(out["NODE_ENV"], "production")

    def test_unresolved_addon_placeholder_raises(self):
        """Unresolved addon placeholder should raise ValueError."""
        env_map = {"DATABASE_URL": "{{POSTGRES_URL}}"}
        with self.assertRaises(ValueError) as ctx:
            _resolve_env_placeholders(env_map, {}, shared_addons={})
        self.assertIn("POSTGRES_URL", str(ctx.exception))

    def test_validate_resolved_env_reports_key_names(self):
        """Validation should report which keys have unresolved placeholders."""
        resolved = {
            "GOOD_KEY": "clean_value",
            "BAD_KEY": "some {{UNRESOLVED}} value",
            "ANOTHER_BAD": "{{ALSO_MISSING}}",
        }
        with self.assertRaises(ValueError) as ctx:
            _validate_resolved_env(resolved)
        msg = str(ctx.exception)
        self.assertIn("BAD_KEY", msg)
        self.assertIn("ANOTHER_BAD", msg)
        self.assertNotIn("GOOD_KEY", msg)

    def test_caprover_placeholders_resolved_to_defaults(self):
        """CAPROVER_ prefixed placeholders should resolve to default values and not crash."""
        env_map = {
            "CAPROVER_URL": "{{CAPROVER_URL}}",
            "CAPROVER_PASSWORD": "{{CAPROVER_PASSWORD}}",
        }
        out = _resolve_env_placeholders(env_map, {})
        self.assertEqual(out["CAPROVER_URL"], "http://localhost")
        self.assertEqual(out["CAPROVER_PASSWORD"], "")

    def test_arbitrary_url_placeholder_not_treated_as_addon(self):
        """An arbitrary URL placeholder should not be treated as a provisionable addon."""
        env_map = {"SOME_OTHER_URL": "{{SOME_OTHER_URL}}"}
        # Should not raise ValueError since it's not a valid addon type
        out = _resolve_env_placeholders(env_map, {})
        self.assertEqual(out["SOME_OTHER_URL"], "{{SOME_OTHER_URL}}")




class PlaceholderAddonTypeTests(SimpleTestCase):
    """Tests for _placeholder_addon_types with embedded placeholders."""

    def test_detects_embedded_postgres_placeholder(self):
        raw_env = {"DATABASE_URL": "{{POSTGRES_URL}}/mydb"}
        types = _placeholder_addon_types(raw_env)
        self.assertIn("POSTGRES", types)

    def test_detects_multiple_addon_types(self):
        raw_env = {
            "DATABASE_URL": "{{POSTGRES_URL}}",
            "CACHE_URL": "{{REDIS_URL}}",
        }
        types = _placeholder_addon_types(raw_env)
        self.assertIn("POSTGRES", types)
        self.assertIn("REDIS", types)

    def test_ignores_service_references(self):
        raw_env = {"API_URL": "{{SERVICE:backend}}"}
        types = _placeholder_addon_types(raw_env)
        self.assertEqual(len(types), 0)

    def test_ignores_shared_secrets(self):
        raw_env = {"JWT_SECRET": "{{SHARED_SECRET:jwt}}"}
        types = _placeholder_addon_types(raw_env)
        self.assertEqual(len(types), 0)


class NormalizeBuildpackTests(SimpleTestCase):
    """Tests for Docker-first build strategy normalization."""

    def test_docker_keyword_returns_docker(self):
        self.assertEqual(_normalize_buildpack("docker"), "DOCKER")
        self.assertEqual(_normalize_buildpack("dockerfile"), "DOCKER")
        self.assertEqual(_normalize_buildpack("Dockerfile"), "DOCKER")

    def test_nixpacks_keyword_returns_nixpacks(self):
        self.assertEqual(_normalize_buildpack("nixpacks"), "NIXPACKS")

    def test_static_keyword_returns_static(self):
        self.assertEqual(_normalize_buildpack("static"), "STATIC")

    def test_empty_or_unknown_defaults_to_docker(self):
        self.assertEqual(_normalize_buildpack(""), "DOCKER")
        self.assertEqual(_normalize_buildpack(None), "DOCKER")
        self.assertEqual(_normalize_buildpack("unknown"), "DOCKER")


class DependencyWaveTests(SimpleTestCase):
    """Tests for dependency graph and wave queueing."""

    def test_services_queued_in_dependency_order(self):
        entries = {
            "db": {"repo": "owner/db", "deploy_order": 1, "depends_on": []},
            "api": {"repo": "owner/api", "deploy_order": 2, "depends_on": ["owner/db"]},
            "frontend": {"repo": "owner/frontend", "deploy_order": 3, "depends_on": ["owner/api"]},
        }
        deps = _resolve_dependency_map(entries)
        waves, unresolved = _build_dependency_waves(entries, deps, wave_size=10)

        # db should be in wave 0, api in wave 1, frontend in wave 2
        flat = [key for wave in waves for key in wave]
        self.assertEqual(flat.index("db") < flat.index("api"), True)
        self.assertEqual(flat.index("api") < flat.index("frontend"), True)
        self.assertEqual(len(unresolved), 0)

    def test_service_env_references_create_dependencies(self):
        entries = {
            "owner/backend": {
                "repo": "owner/backend",
                "name": "backend",
                "requested_name": "backend",
                "deploy_order": 1,
                "depends_on": [],
                "plan": {"env_vars": {}},
            },
            "owner/frontend": {
                "repo": "owner/frontend",
                "name": "frontend",
                "requested_name": "frontend",
                "deploy_order": 1,
                "depends_on": [],
                "plan": {"env_vars": {"API_URL": "{{SERVICE:backend}}"}},
            },
        }

        deps = _resolve_dependency_map(entries)
        self.assertEqual(deps["owner/frontend"], {"owner/backend"})

        waves, unresolved = _build_dependency_waves(entries, deps, wave_size=10)
        self.assertEqual(waves[0], ["owner/backend"])
        self.assertEqual(waves[1], ["owner/frontend"])
        self.assertEqual(unresolved, [])

    def test_independent_services_in_same_wave(self):
        entries = {
            "a": {"repo": "owner/a", "deploy_order": 1, "depends_on": []},
            "b": {"repo": "owner/b", "deploy_order": 1, "depends_on": []},
        }
        deps = _resolve_dependency_map(entries)
        waves, _unresolved = _build_dependency_waves(entries, deps, wave_size=10)

        # Both should be in wave 0
        self.assertEqual(len(waves), 1)
        self.assertEqual(set(waves[0]), {"a", "b"})

    def test_cyclic_dependencies_detected_as_unresolved(self):
        entries = {
            "a": {"repo": "owner/a", "deploy_order": 1, "depends_on": ["owner/b"]},
            "b": {"repo": "owner/b", "deploy_order": 2, "depends_on": ["owner/a"]},
        }
        deps = _resolve_dependency_map(entries)
        _waves, unresolved = _build_dependency_waves(entries, deps, wave_size=10)

        self.assertTrue(len(unresolved) > 0)


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

    @patch("services.ecosystem.fetch_all_repos")
    @patch("services.ecosystem.fetch_repo_tree")
    @patch("services.ecosystem.analyze_ecosystem_chunked")
    @patch("apps.deployments.views_github._get_github_token", return_value="gh-token")
    def test_scan_and_analyze_auto_skips_deployed_services(self, _token_mock, mock_analyze, mock_tree, mock_repos):
        """Verify that scan_and_analyze sets skip=True for already deployed services."""
        mock_repos.return_value = [
            {"full_name": "owner/existing-svc", "default_branch": "main", "private": False, "size": 100},
            {"full_name": "owner/new-svc", "default_branch": "main", "private": False, "size": 100}
        ]
        mock_tree.return_value = ["package.json"]
        mock_analyze.return_value = {
            "services": [
                {"name": "existing-svc", "repo": "owner/existing-svc", "stack": "node", "env_vars": {}, "addons": [], "depends_on": [], "deploy_order": 50},
                {"name": "new-svc", "repo": "owner/new-svc", "stack": "node", "env_vars": {}, "addons": [], "depends_on": [], "deploy_order": 50}
            ],
            "addons": []
        }

        # Deploy existing-svc first
        Service.objects.create(
            owner=self.user,
            name="existing-svc",
            repository_url="https://github.com/owner/existing-svc"
        )

        result = ecosystem_scan_task.run(str(self.user.id), 30)

        services = result.get("services", [])
        self.assertEqual(len(services), 2)

        existing_svc_plan = next(s for s in services if s["name"] == "existing-svc")
        new_svc_plan = next(s for s in services if s["name"] == "new-svc")

        self.assertTrue(existing_svc_plan["skip"])
        self.assertFalse(new_svc_plan["skip"])


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

    @patch("services.addon_provisioner.addon_provisioner.provision", return_value=("postgres-cid", "postgresql://u:p@db:5432/main"))
    @patch("apps.deployments.tasks_ecosystem._queue_wave", return_value=1)
    def test_embedded_postgres_url_with_db_suffix_resolves(self, _queue_wave, _provision):
        """{{POSTGRES_URL}}/identity must become postgres://.../identity."""
        plan = {
            "addons": [{"type": "POSTGRES", "shared_by": ["api"]}],
            "services": [
                {
                    "name": "api",
                    "repo": "https://github.com/owner/api.git",
                    "stack": "django",
                    "port": 8000,
                    "env_vars": {
                        "DATABASE_URL": "{{POSTGRES_URL}}/identity",
                    },
                }
            ],
        }

        with self.settings(SENATE_ENABLED=False):
            result = ecosystem_deploy_task.run(str(self.user.id), plan)

        self.assertEqual(result["failed"], 0)
        service = Service.objects.get(owner=self.user, name="api")
        env = {var.key: var.value for var in EnvironmentVariable.objects.filter(service=service)}
        self.assertEqual(env["DATABASE_URL"], "postgresql://u:p@db:5432/identity")

    @patch("services.addon_provisioner.addon_provisioner.provision", return_value=("postgres-cid", "postgresql://u:p@db:5432/main"))
    @patch("apps.deployments.tasks_ecosystem._queue_wave", return_value=1)
    def test_dockerfile_services_choose_docker_build(self, _queue_wave, _provision):
        """Ecosystem services should default to DOCKER buildpack."""
        plan = {
            "services": [
                {
                    "name": "api",
                    "repo": "https://github.com/owner/api.git",
                    "stack": "django",
                    "port": 8000,
                    "build": "dockerfile",
                    "env_vars": {},
                }
            ],
        }

        with self.settings(SENATE_ENABLED=False):
            result = ecosystem_deploy_task.run(str(self.user.id), plan)

        self.assertEqual(result["failed"], 0)
        service = Service.objects.get(owner=self.user, name="api")
        self.assertEqual(service.buildpack, "DOCKER")

    @patch("services.addon_provisioner.addon_provisioner.provision", return_value=("postgres-cid", "postgresql://u:p@db:5432/main"))
    @patch("apps.deployments.tasks_ecosystem._queue_wave", return_value=1)
    def test_unknown_build_defaults_to_docker(self, _queue_wave, _provision):
        """Unknown/empty build type should default to DOCKER for ecosystem."""
        plan = {
            "services": [
                {
                    "name": "api",
                    "repo": "https://github.com/owner/api.git",
                    "stack": "django",
                    "port": 8000,
                    "env_vars": {},
                }
            ],
        }

        with self.settings(SENATE_ENABLED=False):
            result = ecosystem_deploy_task.run(str(self.user.id), plan)

        self.assertEqual(result["failed"], 0)
        service = Service.objects.get(owner=self.user, name="api")
        self.assertEqual(service.buildpack, "DOCKER")

    def test_service_placeholder_target_database_fallback(self):
        """Verify that _service_placeholder_target queries the database if not in created_services."""
        Service.objects.create(
            owner=self.user,
            name="auth-service",
            provider=self.provider,
            repository_url="https://github.com/owner/auth",
            internal_port=8080,
        )

        host, port = _service_placeholder_target("auth-service", {})
        self.assertEqual(host, "auth-service")
        self.assertEqual(port, 8080)

    @patch("apps.deployments.tasks_ecosystem._queue_wave", return_value=1)
    @patch("services.addon_provisioner.provision", return_value=("mock-cid", "postgresql://new-user:new-pass@new-db:5432/app"))
    def test_addon_no_reuse_user_wide(self, _provision, _queue_wave):
        """Verify that deploying new ecosystem services does NOT reuse unrelated existing user-wide addons."""
        from apps.deployments.models_addons import Addon

        # Create an existing active addon for an unrelated service of the user
        existing_service = Service.objects.create(
            owner=self.user,
            name="core-service",
            provider=self.provider,
            repository_url="https://github.com/owner/core",
        )
        Addon.objects.create(
            service=existing_service,
            name="postgres-shared",
            addon_type="POSTGRES",
            status=Addon.Status.ACTIVE,
            connection_url="postgresql://reused-user:reused-pass@reused-db:5432/app"
        )

        plan = {
            "services": [
                {
                    "name": "new-api",
                    "repo": "owner/new-api",
                    "stack": "node",
                    "port": 3000,
                    "env_vars": {
                        "DATABASE_URL": "{{POSTGRES_URL}}"
                    },
                }
            ]
        }

        with self.settings(SENATE_ENABLED=False):
            result = ecosystem_deploy_task.run(str(self.user.id), plan)

        self.assertEqual(result["failed"], 0)
        new_svc = Service.objects.get(owner=self.user, name="new-api")
        db_url_env = EnvironmentVariable.objects.get(service=new_svc, key="DATABASE_URL")
        self.assertEqual(db_url_env.value, "postgresql://new-user:new-pass@new-db:5432/app")

    def test_heuristic_analysis_is_dynamic(self):
        """Heuristic analysis detects Dockerfile if present, otherwise defaults to nixpacks."""
        from services.ecosystem import heuristic_analysis

        # Scenario A: No Dockerfile present -> nixpacks
        res_nix = heuristic_analysis(["index.js", "package.json"])
        self.assertEqual(res_nix["build"], "nixpacks")

        # Scenario B: Dockerfile present in root -> dockerfile
        res_doc = heuristic_analysis(["index.js", "package.json", "Dockerfile"])
        self.assertEqual(res_doc["build"], "dockerfile")

    def test_simulate_analysis_is_dynamic(self):
        """Simulated DevOps Agent analysis suggests dockerfile for Django, nixpacks for Node."""
        from services.ai_engine import DevOpsAgent
        agent = DevOpsAgent()

        analysis_django = agent._simulate_analysis("https://github.com/owner/django-repo.git")
        self.assertEqual(analysis_django.build_strategy, "dockerfile")

        analysis_node = agent._simulate_analysis("https://github.com/owner/node-repo.git")
        self.assertEqual(analysis_node.build_strategy, "nixpacks")
