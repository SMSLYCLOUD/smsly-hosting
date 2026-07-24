"""Tests for ecosystem planning helpers."""

from django.test import SimpleTestCase
from apps.deployments.services.ecosystem import (
    _apply_generic_ecosystem_intelligence,
    _apply_plan_repo_defaults,
    _build_heuristic_plan,
    _coerce_addons,
    _coerce_depends_on,
    _rebuild_addons_manifest,
)


class EcosystemPlanningHelpersTests(SimpleTestCase):
    def test_apply_plan_repo_defaults_sets_branch_when_missing(self):
        services = [
            {
                "repo": "acme/api-service",
                "name": "api-service",
                "env_vars": {},
            }
        ]
        repos_data = [
            {"repo": "acme/api-service", "default_branch": "master"},
        ]

        _apply_plan_repo_defaults(services, repos_data)

        self.assertEqual(services[0]["branch"], "master")

    def test_build_heuristic_plan_includes_default_branch(self):
        repos_data = [
            {
                "repo": "acme/frontend",
                "default_branch": "release",
                "heuristic": {
                    "stack": "node",
                    "port": 3000,
                    "build": "nixpacks",
                    "addons": [],
                    "env_vars": {},
                },
            }
        ]

        plan = _build_heuristic_plan(repos_data)

        self.assertEqual(len(plan["services"]), 1)
        self.assertEqual(plan["services"][0]["branch"], "release")

    def test_smsly_core_intelligence_wires_dependencies_and_env(self):
        """Test that heuristic intelligence discovers core API and wires it."""
        services = [
            {
                "repo": "acme/worker-service",
                "name": "worker-service",
                "stack": "python",
                "addons": [],
                "env_vars": {"CORE_API_URL": ""},
                "depends_on": [],
                "deploy_order": 5,
            },
            {
                "repo": "acme/platform-api",
                "name": "platform-api",
                "stack": "django",
                "addons": [],
                "env_vars": {},
                "depends_on": [],
                "deploy_order": 9,
            },
        ]

        _apply_generic_ecosystem_intelligence(services)

        core = next(s for s in services if s["name"] == "platform-api")
        worker = next(s for s in services if s["name"] == "worker-service")

        # Verify Core specialized addons (Django defaults)
        self.assertIn("POSTGRES", core["addons"])
        self.assertIn("REDIS", core["addons"])

        # Verify dependency wiring
        self.assertIn("platform-api", worker["depends_on"])
        self.assertEqual(
            worker["env_vars"]["CORE_API_URL"],
            "{{SERVICE:platform-api}}",
        )

    def test_build_heuristic_plan_prioritizes_smsly_core(self):
        """Test that platform-api gets lower deploy_order (higher priority)."""
        repos_data = [
            {
                "repo": "acme/worker-service",
                "default_branch": "main",
                "heuristic": {
                    "stack": "python",
                    "port": 8000,
                    "build": "nixpacks",
                    "addons": [],
                    "env_vars": {"CORE_API_URL": ""},
                },
            },
            {
                "repo": "acme/platform-api",
                "default_branch": "main",
                "heuristic": {
                    "stack": "django",
                    "port": 8080,
                    "build": "nixpacks",
                    "addons": [],
                    "env_vars": {},
                },
            },
        ]

        plan = _build_heuristic_plan(repos_data)
        core = next(s for s in plan["services"] if s["name"] == "platform-api")
        worker = next(s for s in plan["services"] if s["name"] == "worker-service")

        self.assertLess(core["deploy_order"], worker["deploy_order"])
        self.assertIn("platform-api", worker["depends_on"])

    def test_ai_object_shapes_do_not_crash_generic_intelligence(self):
        services = [
            {
                "repo": "acme/platform-api",
                "name": "platform-api",
                "stack": "django",
                "addons": [{"type": "postgres"}, {"type": "redis"}],
                "env_vars": {},
                "depends_on": [],
            },
            {
                "repo": "acme/web",
                "name": "web",
                "stack": "nextjs",
                "addons": [{"type": "redis"}],
                "env_vars": {"PLATFORM_API_URL": ""},
                "depends_on": [{"name": "platform-api"}],
            },
        ]

        _apply_generic_ecosystem_intelligence(services)

        web = next(s for s in services if s["name"] == "web")
        self.assertIn("REDIS", web["addons"])
        self.assertIn("platform-api", web["depends_on"])
        self.assertEqual(web["env_vars"]["PLATFORM_API_URL"], "{{SERVICE:platform-api}}")

    def test_plan_normalizers_accept_ai_dict_entries(self):
        self.assertEqual(
            _coerce_addons([{"type": "postgresql"}, {"type": "cache"}]),
            ["POSTGRES", "REDIS"],
        )
        self.assertEqual(
            _coerce_depends_on([{"service": "api"}, "worker, queue"]),
            ["api", "worker", "queue"],
        )

        manifest = _rebuild_addons_manifest(
            [{"repo": "acme/api", "name": "api", "addons": [{"type": "postgres"}]}],
            [{"type": "redis", "shared_by": [{"name": "web"}]}],
        )

        self.assertEqual(
            manifest,
            [
                {"type": "POSTGRES", "shared_by": ["api"]},
                {"type": "REDIS", "shared_by": ["web"]},
            ],
        )
