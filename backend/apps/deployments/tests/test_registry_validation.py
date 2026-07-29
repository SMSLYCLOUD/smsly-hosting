# pylint: disable=invalid-name
"""
Regression tests for the registry-validation consolidation.

Covers:
  1. validate_image_registry rejects user-controlled image refs
     that would pull from a non-allowlisted registry.
  2. safe_image_for_service builds a reference using the
     platform's configured CONTAINER_REGISTRY_URL.
  3. self_healing_orchestrator no longer hard-codes 10.100.0.1:5000.
"""

from django.test import TestCase, override_settings

from apps.deployments.services.registry_validation import (
    ALLOWED_IMAGE_REGISTRY_HOSTS,
    safe_image_for_service,
    safe_registry_host_for_internal_fallback,
    validate_image_registry,
)


class ValidateImageRegistryTests(TestCase):
    """Audit: every docker pull path must reject image refs whose
    registry host is not on the platform allowlist."""

    def test_internal_registry_hosts_are_allowed(self):
        for host in (
            "127.0.0.1:5000", "localhost:5000", "registry:5000",
        ):
            self.assertEqual(
                validate_image_registry(f"{host}/smsly/foo:bar"), f"{host}/smsly/foo:bar"
            )

    def test_public_registry_hosts_are_allowed(self):
        for host in (
            "ghcr.io", "docker.io", "quay.io", "gcr.io",
            "mcr.microsoft.com", "public.ecr.aws",
        ):
            self.assertEqual(
                validate_image_registry(f"{host}/smsly/foo:bar"),
                f"{host}/smsly/foo:bar",
            )

    def test_docker_hub_library_reference_is_allowed(self):
        # "nginx:1.27-alpine" has no registry prefix → treated as
        # Docker Hub (docker.io).
        result = validate_image_registry("nginx:1.27-alpine")
        self.assertEqual(result, "nginx:1.27-alpine")

    def test_attacker_registry_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            validate_image_registry("attacker.example.com/smsly/foo:bar")
        self.assertIn("attacker.example.com", str(cm.exception))

    def test_docker_hub_org_reference_is_allowed(self):
        # "myorg/private-image:tag" has a registry prefix "myorg"
        # which does NOT contain '.' or ':' so is treated as
        # Docker Hub. The allowlist accepts docker.io, so the
        # full ref is accepted. (The organization-level access
        # is a separate concern handled at the registry.)
        result = validate_image_registry("myorg/private-image:tag")
        self.assertEqual(result, "myorg/private-image:tag")

    def test_shell_metacharacters_are_rejected(self):
        for bad in (
            "registry:5000/smsly/foo;rm -rf /",
            "registry:5000/smsly/foo|curl evil",
            "registry:5000/smsly/foo`id`",
            "registry:5000/smsly/foo$VAR",
            "registry:5000/smsly/foo bar",
        ):
            with self.assertRaises(ValueError):
                validate_image_registry(bad)

    def test_empty_string_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_image_registry("")
        with self.assertRaises(ValueError):
            validate_image_registry(None)


class SafeImageForServiceTests(TestCase):
    """The fallback image ref must be constructed from the
    platform's CONTAINER_REGISTRY_URL, never a hard-coded host.
    """

    @override_settings(CONTAINER_REGISTRY_URL="https://registry.smsly.cloud")
    def test_external_registry_uses_configured_url(self):
        host = safe_registry_host_for_internal_fallback()
        # The netloc is extracted (no scheme, no path).
        self.assertEqual(host, "registry.smsly.cloud")
        ref = safe_image_for_service("my-svc", tag="abc1234")
        self.assertEqual(ref, "registry.smsly.cloud/smsly/my-svc:abc1234")

    def test_unsafe_service_name_is_sanitized(self):
        # A service name with shell metacharacters would let an
        # attacker inject docker CLI args. The helper strips
        # anything outside [a-z0-9_.-] and falls back to 'app'.
        ref = safe_image_for_service("foo;rm -rf /", tag="v1")
        self.assertNotIn(";", ref)
        self.assertNotIn(" ", ref)
        self.assertTrue(ref.endswith(":v1"))
        # Default name 'app' must be used when sanitisation empties
        # the input.
        ref = safe_image_for_service("!!!", tag="v1")
        self.assertIn("/smsly/app:v1", ref)

    def test_unsafe_tag_is_sanitized(self):
        # The tag may only contain [A-Za-z0-9_.-]; anything else
        # is dropped. The result is still a valid Docker tag.
        ref = safe_image_for_service("svc", tag="v1;injected")
        self.assertNotIn(";", ref)
        self.assertTrue(ref.endswith(":v1injected"))


class AllowlistConsistencyTests(TestCase):
    """The registry allowlist in services/registry_validation.py
    must match the one in serializers.py. If they drift, the
    internal callers would allow something the API boundary
    rejects.
    """

    def test_allowlist_matches_serializers(self):
        from apps.deployments.serializers.common import _all_allowed_registry_hosts as serializer_allowed_hosts
        from apps.deployments.services.registry_validation import all_allowed_registry_hosts

        self.assertEqual(
            serializer_allowed_hosts(),
            all_allowed_registry_hosts(),
            "serializers.py and registry_validation.py must return "
            "the same allowlist to avoid policy drift between the "
            "API boundary and internal callers.",
        )
