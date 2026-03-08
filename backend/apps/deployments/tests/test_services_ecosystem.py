"""Tests for ecosystem planning helpers."""

from django.test import SimpleTestCase

from services.ecosystem import (
    _apply_plan_repo_defaults,
    _apply_smsly_core_intelligence,
    _build_heuristic_plan,
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
        services = [
            {
                "repo": "acme/smsly-sms",
                "name": "smsly-sms",
                "stack": "python",
                "addons": [],
                "env_vars": {},
                "depends_on": [],
                "deploy_order": 5,
            },
            {
                "repo": "acme/smsly-core",
                "name": "smsly-core",
                "stack": "django",
                "addons": [],
                "env_vars": {},
                "depends_on": [],
                "deploy_order": 9,
            },
        ]

        _apply_smsly_core_intelligence(services)

        core = next(s for s in services if s["name"] == "smsly-core")
        sms = next(s for s in services if s["name"] == "smsly-sms")

        self.assertEqual(core["deploy_order"], 1)
        self.assertIn("POSTGRES", core["addons"])
        self.assertIn("REDIS", core["addons"])
        self.assertIn("smsly-core", sms["depends_on"])
        self.assertEqual(
            sms["env_vars"]["SMSLY_PLATFORM_API_URL"],
            "{{SERVICE:smsly-core}}",
        )

    def test_build_heuristic_plan_prioritizes_smsly_core(self):
        repos_data = [
            {
                "repo": "acme/smsly-sms",
                "default_branch": "main",
                "heuristic": {
                    "stack": "python",
                    "port": 8000,
                    "build": "nixpacks",
                    "addons": [],
                    "env_vars": {},
                },
            },
            {
                "repo": "acme/smsly-core",
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
        core = next(s for s in plan["services"] if s["name"] == "smsly-core")
        sms = next(s for s in plan["services"] if s["name"] == "smsly-sms")

        self.assertLess(core["deploy_order"], sms["deploy_order"])
        self.assertIn("smsly-core", sms["depends_on"])
