"""
Regression tests for Issue 78.

``_build_targets`` must respect the per-server ``verify_tls`` flag
for the internal/mesh/private/container probe URLs. The previous
code hard-coded ``verify=False`` for these URLs.
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, ManagedServer, Service
from apps.core.services import health_monitor as hm


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "verify-tls-test",
        }
    }
)
class HealthMonitorVerifyTlsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="tls-tester", password="p",
        )
        self.provider = CloudProvider.objects.create(
            name="p", provider_type=CloudProvider.ProviderType.LOCAL, is_active=True,
        )

    def tearDown(self):
        cache.clear()

    def _make_service(self, verify_tls: bool) -> Service:
        server = ManagedServer.objects.create(
            owner=self.user,
            name=f"server-{verify_tls}",
            host="198.51.100.20",
            private_ip="172.16.0.5",
            verify_tls=verify_tls,
        )
        service = Service.objects.create(
            name=f"svc-tls-{verify_tls}",
            owner=self.user,
            provider=self.provider,
            server=server,
            health_check_path="/health",
            internal_port=8000,
        )
        Deployment.objects.create(
            service=service,
            commit_hash="a" * 40,
            status=Deployment.Status.ACTIVE,
            container_id="container-xyz",
        )
        return service

    def _assert_targets_respect(self, service, expected: bool):
        active = (
            Deployment.objects.filter(
                service=service, status=Deployment.Status.ACTIVE,
            ).order_by("-created_at").first()
        )
        targets = hm._build_targets(service, active)
        # Filter to the internal/mesh/private/container targets we
        # care about (the public_domain-derived target uses a different
        # code path).
        for target in targets:
            url = target["url"]
            if "container-xyz" in url:
                # Container target should respect verify_tls.
                self.assertEqual(
                    target["verify"], expected,
                    f"container target {url} verify={target['verify']}",
                )
            if service.server.private_ip and service.server.private_ip in url:
                self.assertEqual(
                    target["verify"], expected,
                    f"private_ip target {url} verify={target['verify']}",
                )

    def test_private_and_container_targets_use_per_server_verify_tls_true(self):
        service = self._make_service(verify_tls=True)
        self._assert_targets_respect(service, expected=True)

    def test_private_and_container_targets_use_per_server_verify_tls_false(self):
        service = self._make_service(verify_tls=False)
        self._assert_targets_respect(service, expected=False)

    def test_server_verify_tls_helper_defaults_to_true(self):
        # A server without an explicit verify_tls attribute still
        # defaults to True.
        self.assertTrue(hm._server_verify_tls(None))
        # A server with verify_tls=False returns False.
        server = ManagedServer(
            owner=self.user, name="x", host="198.51.100.30",
            verify_tls=False,
        )
        self.assertFalse(hm._server_verify_tls(server))
