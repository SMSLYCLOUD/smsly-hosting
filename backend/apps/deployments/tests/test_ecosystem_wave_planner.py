# pylint: disable=invalid-name
"""Unit tests for ecosystem wave planning."""

from django.test import SimpleTestCase

from apps.deployments.tasks.ecosystem.helpers import (
    _build_dependency_waves,
    _resolve_dependency_map,
)


class EcosystemWavePlannerTests(SimpleTestCase):
    def test_build_dependency_waves_with_chunking(self):
        entries = {
            "owner/api": {"deploy_order": 1},
            "owner/worker": {"deploy_order": 2},
            "owner/web": {"deploy_order": 3},
            "owner/audit": {"deploy_order": 4},
        }
        dependencies = {
            "owner/api": set(),
            "owner/worker": {"owner/api"},
            "owner/web": {"owner/api"},
            "owner/audit": {"owner/worker", "owner/web"},
        }

        waves, unresolved = _build_dependency_waves(entries, dependencies, wave_size=2)

        self.assertEqual(unresolved, [])
        self.assertEqual(waves, [["owner/api"], ["owner/worker", "owner/web"], ["owner/audit"]])

    def test_cycle_is_returned_as_unresolved(self):
        entries = {
            "owner/a": {"deploy_order": 1},
            "owner/b": {"deploy_order": 2},
        }
        dependencies = {
            "owner/a": {"owner/b"},
            "owner/b": {"owner/a"},
        }

        waves, unresolved = _build_dependency_waves(entries, dependencies, wave_size=10)

        self.assertEqual(set(unresolved), {"owner/a", "owner/b"})
        self.assertEqual(len(waves), 1)
        self.assertEqual(set(waves[0]), {"owner/a", "owner/b"})

    def test_mixed_dag_and_cycles_wave_all_with_cyclic_last(self):
        # Shape of the 2026-09-25 outage: AI-declared mutual runtime
        # refs (backend<->gateway) alongside a clean DAG. The caller
        # deploys unresolved nodes last instead of failing the plan,
        # so every node must appear in the waves exactly once.
        entries = {
            "org/policy": {"deploy_order": 1},
            "org/backend": {"deploy_order": 2},
            "org/gateway": {"deploy_order": 3},
            "org/frontend": {"deploy_order": 4},
            "org/rate-limit": {"deploy_order": 5},
        }
        dependencies = {
            "org/policy": set(),
            "org/backend": {"org/gateway"},
            "org/gateway": {"org/backend"},
            "org/frontend": {"org/backend"},
            "org/rate-limit": {"org/backend"},
        }

        waves, unresolved = _build_dependency_waves(entries, dependencies, wave_size=10)

        # backend<->gateway is a 2-cycle; frontend/rate-limit hang off
        # the cycle so they can never reach indegree 0 either. policy
        # is the only clean node.
        self.assertEqual(
            set(unresolved),
            {"org/backend", "org/gateway", "org/frontend", "org/rate-limit"},
        )
        flat = [key for wave in waves for key in wave]
        self.assertEqual(sorted(flat), sorted(entries))
        # Resolvable nodes wave before anything tainted by the cycle.
        self.assertEqual(flat[0], "org/policy")
        self.assertEqual(
            flat[1:],
            ["org/backend", "org/gateway", "org/frontend", "org/rate-limit"],
        )

    def test_dependency_aliases_resolve_to_repo_keys(self):
        entries_by_key = {
            "org/api-service": {
                "repo": "org/api-service",
                "name": "api-service",
                "requested_name": "api-service",
                "depends_on": [],
            },
            "org/web-app": {
                "repo": "org/web-app",
                "name": "web-app",
                "requested_name": "web-app",
                "depends_on": ["api-service"],
            },
        }

        resolved = _resolve_dependency_map(entries_by_key)

        self.assertEqual(resolved["org/api-service"], set())
        self.assertEqual(resolved["org/web-app"], {"org/api-service"})
