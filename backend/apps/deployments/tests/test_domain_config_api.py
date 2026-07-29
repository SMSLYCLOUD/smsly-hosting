# pylint: disable=invalid-name
"""Tests for platform domain/SSL configuration API."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.deployments.models import EnvironmentVariable, PlatformConfig, Service

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "domain-config-tests",
    }
}


@override_settings(CACHES=TEST_CACHES)
class DomainConfigApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="domain-admin",
            email="domain-admin@example.com",
            password="password123",
        )
        self.client.force_authenticate(user=self.admin)
        self.url = "/api/v1/system/domain-config/"

        cfg = PlatformConfig.load()
        cfg.domain = "cloud.smsly.cloud"
        cfg.use_ssl = True
        cfg.wildcard_subdomains = False
        cfg.cloudflare_api_token = "existing-token"
        cfg.save()

    @patch("apps.deployments.services.caddy_manager.apply_caddyfile", return_value={"ok": True, "message": "ok"})
    @patch("apps.deployments.services.caddy_manager.generate_caddyfile", return_value=":80 { reverse_proxy localhost:8090 }")
    def test_put_without_token_field_keeps_existing_token(self, _gen_mock, apply_mock):
        response = self.client.put(
            self.url,
            {
                "domain": "cloud.smsly.cloud",
                "use_ssl": True,
                "wildcard_subdomains": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cfg = PlatformConfig.load()
        self.assertEqual(cfg.cloudflare_api_token, "existing-token")
        self.assertEqual(
            apply_mock.call_args.kwargs.get("cloudflare_token"),
            "existing-token",
        )

    @patch("apps.deployments.services.caddy_manager.apply_caddyfile", return_value={"ok": True, "message": "ok"})
    @patch("apps.deployments.services.caddy_manager.generate_caddyfile", return_value=":80 { reverse_proxy localhost:8090 }")
    def test_put_with_empty_token_clears_existing_token(self, _gen_mock, apply_mock):
        response = self.client.put(
            self.url,
            {
                "use_ssl": False,
                "wildcard_subdomains": False,
                "cloudflare_api_token": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cfg = PlatformConfig.load()
        self.assertEqual(cfg.cloudflare_api_token, "")
        self.assertEqual(
            apply_mock.call_args.kwargs.get("cloudflare_token"),
            "",
        )

    def test_cannot_enable_wildcard_ssl_without_token(self):
        response = self.client.put(
            self.url,
            {
                "use_ssl": True,
                "wildcard_subdomains": True,
                "cloudflare_api_token": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        cfg = PlatformConfig.load()
        self.assertEqual(cfg.cloudflare_api_token, "existing-token")

    @patch("apps.deployments.services.caddy_manager.apply_caddyfile", return_value={"ok": True, "message": "ok"})
    @patch("apps.deployments.services.caddy_manager.generate_caddyfile", return_value=":80 { reverse_proxy localhost:8090 }")
    def test_put_rewrites_existing_service_public_domains(self, _gen_mock, _apply_mock):
        service = Service.objects.create(name="marketer", owner=self.admin)
        original_public_domain = service.public_domain
        EnvironmentVariable.objects.create(
            service=service,
            key="PUBLIC_DOMAIN",
            value=service.public_domain,
            is_secret=False,
        )
        EnvironmentVariable.objects.create(
            service=service,
            key="ALLOWED_HOSTS",
            value=f"localhost,{service.public_domain}",
            is_secret=False,
        )
        external = Service.objects.create(
            name="custom-domain-service",
            owner=self.admin,
            public_domain="kept.external.example.com",
        )

        response = self.client.put(
            self.url,
            {
                "domain": "pcloud.linadeluxe.com",
                "use_ssl": True,
                "wildcard_subdomains": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["updated_service_domains"], 1)
        self.assertTrue(response.data["redeploy_required"])

        service.refresh_from_db()
        external.refresh_from_db()
        expected_public_domain = original_public_domain.replace(
            ".cloud.smsly.cloud",
            ".pcloud.linadeluxe.com",
        )
        self.assertEqual(service.public_domain, expected_public_domain)
        self.assertEqual(external.public_domain, "kept.external.example.com")

        public_domain_env = EnvironmentVariable.objects.get(service=service, key="PUBLIC_DOMAIN")
        self.assertEqual(public_domain_env.value, service.public_domain)

        allowed_hosts_env = EnvironmentVariable.objects.get(service=service, key="ALLOWED_HOSTS")
        self.assertEqual(
            allowed_hosts_env.value,
            f"localhost,{service.public_domain}",
        )
