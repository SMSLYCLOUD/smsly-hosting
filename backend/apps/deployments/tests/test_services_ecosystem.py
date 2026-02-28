"""Tests for ecosystem planning helpers."""

from django.test import SimpleTestCase

from services.ecosystem import _apply_plan_repo_defaults, _build_heuristic_plan


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
