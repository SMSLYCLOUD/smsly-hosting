# pylint: disable=invalid-name
"""
Regression tests for the centralised TLS verification policy.

The audit found 18+ scattered ``verify=False`` calls across the
codebase. All of them should now route through
``apps.deployments.services.tls_verify.should_verify(url)`` so that
the policy (loopback, Docker-internal, private-with-env-var, plain
HTTP → verify=False; public HTTPS → verify=True) lives in exactly
one place.

This test pins down that policy so future refactors can't accidentally
re-introduce a hard-coded ``verify=False`` against a public target.
"""
import logging
import os
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.deployments.services import tls_verify


class ShouldVerifyTests(SimpleTestCase):
    """Core policy tests for ``should_verify(url)``."""

    def test_plain_http_loopback_does_not_require_verification(self):
        self.assertFalse(tls_verify.should_verify("http://localhost:8000/health"))
        self.assertFalse(tls_verify.should_verify("http://127.0.0.1:8000/health"))
        self.assertFalse(tls_verify.should_verify("http://[::1]:8000/health"))

    def test_docker_internal_hostname_does_not_require_verification(self):
        # Known Docker DNS names are always internal.
        for name in ("backend", "db", "redis", "rabbitmq", "registry",
                     "caddy", "traefik", "frontend", "celery", "pgcat"):
            self.assertFalse(
                tls_verify.should_verify(f"http://{name}:8000/health"),
                f"{name} should be treated as Docker-internal",
            )

    def test_https_public_requires_verification(self):
        self.assertTrue(
            tls_verify.should_verify("https://api.example.com/health")
        )
        self.assertTrue(
            tls_verify.should_verify("https://smsly.cloud/health")
        )

    def test_https_loopback_does_not_require_verification(self):
        self.assertFalse(tls_verify.should_verify("https://localhost/health"))
        self.assertFalse(tls_verify.should_verify("https://127.0.0.1/health"))

    @override_settings(DEBUG=False)
    def test_https_private_ip_requires_verification_by_default(self):
        # RFC 1918 — without the env var and without DEBUG, must verify.
        with mock.patch.dict(os.environ, {"ALLOW_INSECURE_INTER_NODE_TLS": ""}):
            self.assertTrue(
                tls_verify.should_verify("https://10.0.0.1:8000/health")
            )
            self.assertTrue(
                tls_verify.should_verify("https://192.168.1.5:8000/health")
            )
            self.assertTrue(
                tls_verify.should_verify("https://172.16.0.5:8000/health")
            )

    @override_settings(DEBUG=False)
    def test_https_private_ip_skips_verification_when_env_var_set(self):
        with mock.patch.dict(os.environ, {"ALLOW_INSECURE_INTER_NODE_TLS": "true"}):
            self.assertFalse(
                tls_verify.should_verify("https://10.0.0.1:8000/health")
            )
            self.assertFalse(
                tls_verify.should_verify("https://192.168.1.5:8000/health")
            )

    @override_settings(DEBUG=True)
    def test_https_private_ip_skips_verification_in_debug(self):
        # Django DEBUG acts as a development-mode override.
        with mock.patch.dict(os.environ, {"ALLOW_INSECURE_INTER_NODE_TLS": ""}):
            self.assertFalse(
                tls_verify.should_verify("https://10.0.0.1:8000/health")
            )

    def test_https_unresolvable_hostname_treated_as_public(self):
        # A hostname that doesn't resolve and isn't on the Docker-internal
        # allow-list should default to verify=True (the safe default).
        self.assertTrue(
            tls_verify.should_verify("https://definitely-not-a-real-host-xyz.invalid/health")
        )

    def test_empty_url_has_no_scheme(self):
        # Degenerate input: no scheme means we can't verify anything, so
        # the helper returns False (consistent with plain HTTP).
        self.assertFalse(tls_verify.should_verify(""))


class IsInsecureTargetTests(SimpleTestCase):
    """Tests for the ``is_insecure_target`` audit helper."""

    def test_plain_http_is_insecure(self):
        insecure, reason = tls_verify.is_insecure_target("http://example.com/x")
        self.assertTrue(insecure)
        self.assertEqual(reason, "plain-http")

    def test_https_loopback_is_insecure(self):
        insecure, reason = tls_verify.is_insecure_target("https://localhost/x")
        self.assertTrue(insecure)
        self.assertEqual(reason, "loopback")

    def test_https_docker_internal_is_insecure(self):
        insecure, reason = tls_verify.is_insecure_target("https://backend:8000/x")
        self.assertTrue(insecure)
        self.assertEqual(reason, "docker-internal")

    def test_https_public_is_secure(self):
        insecure, reason = tls_verify.is_insecure_target("https://api.example.com/x")
        self.assertFalse(insecure)
        self.assertEqual(reason, "public")

    def test_https_private_without_env_var_is_secure(self):
        with override_settings(DEBUG=False):
            with mock.patch.dict(os.environ, {"ALLOW_INSECURE_INTER_NODE_TLS": ""}):
                insecure, reason = tls_verify.is_insecure_target("https://10.0.0.1/x")
                self.assertFalse(insecure)
                self.assertEqual(reason, "private-network-not-allowed")


class AuditVerifyTests(SimpleTestCase):
    """Tests for the ``audit_verify`` warning logger."""

    def test_no_warning_when_verify_true(self):
        logger = logging.getLogger("apps.deployments.services.tls_verify")
        with self.assertNoLogs(logger, level="WARNING"):
            tls_verify.audit_verify("https://api.example.com/health", True)

    def test_no_warning_for_loopback_with_verify_false(self):
        logger = logging.getLogger("apps.deployments.services.tls_verify")
        with self.assertNoLogs(logger, level="WARNING"):
            tls_verify.audit_verify("http://localhost:8000/health", False)

    def test_warning_for_public_https_with_verify_false(self):
        logger = logging.getLogger("apps.deployments.services.tls_verify")
        with self.assertLogs(logger, level="WARNING") as captured:
            tls_verify.audit_verify("https://api.example.com/health", False)
        self.assertEqual(len(captured.records), 1)
        msg = captured.records[0].getMessage()
        self.assertIn("api.example.com", msg)
        self.assertIn("ALLOW_INSECURE_INTER_NODE_TLS", msg)


class NoBareVerifyFalseCallSitesTests(SimpleTestCase):
    """Sanity check: the four known call-site files must not contain a
    bare ``verify=False`` any more (i.e. it must be routed through
    ``should_verify``). Comments and docstrings are excluded by matching
    the exact token preceded by a non-comment character."""

    BAD_FILES = (
        "apps/deployments/services/health_monitor.py",
        "apps/deployments/services/remote_orchestrator.py",
        "apps/deployments/tasks.py",
        "apps/deployments/management/commands/diagnose_nodes.py",
    )

    def test_no_bare_verify_false_in_call_sites(self):
        import inspect

        from apps.deployments.services import (
            health_monitor,
            remote_orchestrator,
        )
        from apps.deployments.management.commands import diagnose_nodes
        import apps.deployments.tasks as tasks_module

        modules = {
            "health_monitor.py": health_monitor,
            "remote_orchestrator.py": remote_orchestrator,
            "tasks.py": tasks_module,
            "diagnose_nodes.py": diagnose_nodes,
        }
        for filename, module in modules.items():
            source = inspect.getsource(module)
            # Strip comment-only lines so docstrings/comment mentions of
            # ``verify=False`` don't trip the check.
            non_comment_lines = [
                line for line in source.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            non_comment_source = "\n".join(non_comment_lines)
            self.assertNotIn(
                "verify=False",
                non_comment_source,
                f"{filename} still contains a bare 'verify=False' "
                f"outside of comments; route it through should_verify(url) "
                f"in apps.deployments.services.tls_verify.",
            )
