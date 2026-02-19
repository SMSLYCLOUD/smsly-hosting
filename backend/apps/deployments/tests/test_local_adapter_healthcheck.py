"""Unit tests for LocalAdapter healthcheck probe generation."""

from django.test import SimpleTestCase

from apps.cloud.adapters.local import (
    _build_docker_healthcheck_cmd,
    _normalize_health_path,
)


class LocalAdapterHealthcheckCommandTests(SimpleTestCase):
    """Ensure generated probe commands are resilient across base images."""

    def test_normalize_health_path_prefixes_slash(self):
        self.assertEqual(_normalize_health_path("health"), "/health")
        self.assertEqual(_normalize_health_path("/health"), "/health")
        self.assertEqual(_normalize_health_path(""), "/")

    def test_healthcheck_command_has_tool_fallbacks(self):
        cmd = _build_docker_healthcheck_cmd("http://localhost:8000/health", 5)

        self.assertIn("command -v wget", cmd)
        self.assertIn("command -v curl", cmd)
        self.assertIn("command -v python3", cmd)
        self.assertIn("command -v python", cmd)
        self.assertIn("exit 0", cmd)
        self.assertIn("exit 1", cmd)

