"""Project-scoped image namespaces + Envoy sidecar image helpers.

Locks in:
  * ``project_image_namespace``: ECOSYSTEM services with a project build
    into ``proj-<id8>`` (same identity as their registry credential);
    everything else keeps the legacy global ``smsly/`` namespace.
  * ``_project_namespace_prefix`` (registry_auth) stays identical to it.
  * ``safe_image_for_service`` honors an explicit namespace.
  * ``EnvoySidecar._split_image_ref`` parses refs with registry ports.
  * ``_platform_registry_auth`` degrades to empty creds (never crashes)
    when credential resolution is unavailable.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.deployments.services.registry_credentials import (
    project_image_namespace,
    project_registry_username,
)
from apps.deployments.services.registry_validation import safe_image_for_service
from apps.deployments.views.registry_auth import _project_namespace_prefix
from apps.mtls.services.envoy_sidecar import EnvoySidecar


def _svc(managed_by="ECOSYSTEM", project_id=None):
    return SimpleNamespace(managed_by=managed_by, project_id=project_id)


class ProjectImageNamespaceTests(SimpleTestCase):
    def test_ecosystem_with_project_is_scoped(self):
        pid = uuid.uuid4()
        expected = f"proj-{str(pid).replace('-', '')[:8]}"
        self.assertEqual(project_image_namespace(_svc("ECOSYSTEM", pid)), expected)
        # Namespace identity matches the credential identity.
        self.assertEqual(project_registry_username(pid), expected)

    def test_auth_view_prefix_matches_helper(self):
        pid = uuid.uuid4()
        svc_ns = project_image_namespace(_svc("ECOSYSTEM", pid))
        self.assertEqual(_project_namespace_prefix(pid), svc_ns)

    def test_ecosystem_without_project_stays_global(self):
        self.assertEqual(project_image_namespace(_svc("ECOSYSTEM", None)), "smsly")

    def test_manual_service_with_project_stays_global(self):
        self.assertEqual(
            project_image_namespace(_svc("USER", uuid.uuid4())), "smsly"
        )

    def test_missing_attrs_stays_global(self):
        self.assertEqual(project_image_namespace(SimpleNamespace()), "smsly")
        self.assertEqual(project_image_namespace(_svc("", None)), "smsly")


class SafeImageNamespaceTests(TestCase):
    """safe_image_for_service touches the DB (mesh-IP lookup) like the
    existing validation tests, so this needs TestCase, not SimpleTestCase."""
    def test_default_namespace_is_legacy_global(self):
        ref = safe_image_for_service("my-svc", tag="abc1234")
        self.assertIn("/smsly/my-svc:abc1234", ref)

    def test_explicit_namespace_is_used_and_sanitized(self):
        ref = safe_image_for_service("my-svc", tag="abc1234", namespace="proj-a1b2c3d4")
        self.assertIn("/proj-a1b2c3d4/my-svc:abc1234", ref)
        ref = safe_image_for_service("my-svc", tag="t", namespace="PROJ-XYZ;RM")
        self.assertNotIn(";", ref)
        self.assertNotIn("PROJ", ref)


class SplitImageRefTests(SimpleTestCase):
    def test_registry_with_port(self):
        host, repo, tag = EnvoySidecar._split_image_ref(
            "registry:5000/smsly/envoy-spire-sidecar:latest"
        )
        self.assertEqual(host, "registry:5000")
        self.assertEqual(repo, "registry:5000/smsly/envoy-spire-sidecar")
        self.assertEqual(tag, "latest")

    def test_dotted_registry(self):
        host, repo, tag = EnvoySidecar._split_image_ref("ghcr.io/smsly/x:v1")
        self.assertEqual((host, repo, tag), ("ghcr.io", "ghcr.io/smsly/x", "v1"))

    def test_unqualified_name(self):
        host, repo, tag = EnvoySidecar._split_image_ref("envoyproxy/envoy:v1")
        self.assertEqual(host, "")
        self.assertEqual(tag, "v1")

    def test_missing_tag_defaults_latest(self):
        host, repo, tag = EnvoySidecar._split_image_ref("registry:5000/smsly/x")
        self.assertEqual(tag, "latest")
        self.assertEqual(repo, "registry:5000/smsly/x")


class PlatformRegistryAuthTests(SimpleTestCase):
    def test_fallback_when_unresolvable(self):
        with patch(
            "apps.deployments.models.registry_scope.ScopedRegistry.resolve_registry_credentials",
            side_effect=RuntimeError("no db"),
        ):
            self.assertEqual(
                EnvoySidecar._platform_registry_auth(), ("registry:5000", "", "")
            )
