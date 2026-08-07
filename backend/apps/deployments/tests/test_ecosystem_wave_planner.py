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
