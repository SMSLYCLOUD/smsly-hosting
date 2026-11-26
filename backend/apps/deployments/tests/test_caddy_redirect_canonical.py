from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.services.caddy_manager.config_generation import (
    _get_wildcard_known_hosts,
    _get_wildcard_redirect_map,
    _www_apex_redirect_target,
)
from apps.deployments.services.caddy_manager.validation import (
    validate_wildcard_redirects_authoritative,
)
from apps.domains.models import Domain, DomainStatus


class WwwApexCanonicalTests(SimpleTestCase):
    def test_www_redirects_to_verified_apex(self):
        self.assertEqual(
            _www_apex_redirect_target("www.trulay.co", {"trulay.co", "www.trulay.co"}),
            "trulay.co",
        )

    def test_www_only_setup_keeps_proxying(self):
        self.assertEqual(_www_apex_redirect_target("www.trulay.co", {"www.trulay.co"}), "")

    def test_apex_and_subdomains_never_redirect(self):
        self.assertEqual(_www_apex_redirect_target("trulay.co", {"trulay.co"}), "")
        self.assertEqual(
            _www_apex_redirect_target("app.trulay.co", {"app.trulay.co", "trulay.co"}),
            "",
        )

    def test_www_matching_other_services_apex_does_not_redirect(self):
        # The verified set is per-service: www.X redirects only to X when
        # X belongs to the SAME service. A foreign apex must not trigger it.
        self.assertEqual(_www_apex_redirect_target("www.a.com", {"b.com"}), "")


class WildcardRedirectShadowTests(SimpleTestCase):
    def test_shadowed_redirect_is_refused(self):
        content = (
            "*.grid.smsly.cloud {\n"
            "    @wildcard_redirect_0 host svc-1.grid.smsly.cloud\n"
            "    handle @wildcard_redirect_0 {\n"
            "        redir https://example.com{uri} 301\n"
            "    }\n"
            "}\n"
            "svc-1.grid.smsly.cloud {\n"
            "    reverse_proxy traefik:80\n"
            "}\n"
        )
        errors = validate_wildcard_redirects_authoritative(content)
        self.assertEqual(len(errors), 1)
        self.assertIn("svc-1.grid.smsly.cloud", errors[0])

    def test_authoritative_redirect_passes(self):
        content = (
            "*.grid.smsly.cloud {\n"
            "    @wildcard_redirect_0 host svc-1.grid.smsly.cloud\n"
            "    handle @wildcard_redirect_0 {\n"
            "        redir https://example.com{uri} 301\n"
            "    }\n"
            "}\n"
        )
        self.assertEqual(validate_wildcard_redirects_authoritative(content), [])

    def test_no_redirects_passes(self):
        self.assertEqual(
            validate_wildcard_redirects_authoritative("grid.smsly.cloud {\n}\n"), []
        )


class WildcardRedirectVerifiedTargetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="redir-owner", email="redir@example.com", password="password123"
        )
        self.provider = CloudProvider.objects.create(
            name="redir-prov",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="redir-svc",
            owner=self.user,
            provider=self.provider,
            public_domain="redir-svc.grid.smsly.cloud",
            custom_domains=["www.example.org", "example.org"],
            wildcard_redirect_custom_domain=True,
        )

    def test_unverified_custom_domain_does_not_redirect_and_stays_in_known_hosts(self):
        Domain.objects.create(
            domain_name="www.example.org",
            service=self.service,
            status=DomainStatus.DNS_PENDING,
            verified=False,
        )
        Domain.objects.create(
            domain_name="example.org",
            service=self.service,
            status=DomainStatus.DNS_PENDING,
            verified=False,
        )
        self.assertEqual(_get_wildcard_redirect_map("grid.smsly.cloud"), {})
        self.assertIn("redir-svc.grid.smsly.cloud", _get_wildcard_known_hosts("grid.smsly.cloud"))

    def test_verified_custom_domain_redirects_wildcard_and_canonicalizes_www(self):
        Domain.objects.create(
            domain_name="www.example.org",
            service=self.service,
            status=DomainStatus.DNS_VERIFIED,
            verified=True,
        )
        Domain.objects.create(
            domain_name="example.org",
            service=self.service,
            status=DomainStatus.DNS_VERIFIED,
            verified=True,
        )
        self.assertEqual(
            _get_wildcard_redirect_map("grid.smsly.cloud"),
            {"redir-svc.grid.smsly.cloud": "example.org"},
        )
        self.assertNotIn("redir-svc.grid.smsly.cloud", _get_wildcard_known_hosts("grid.smsly.cloud"))

    def test_private_service_does_not_emit_wildcard_redirect(self):
        Domain.objects.create(
            domain_name="example.org",
            service=self.service,
            status=DomainStatus.ACTIVE,
            verified=True,
        )
        self.service.is_public = False
        self.service.save(update_fields=["is_public"])
        self.assertEqual(_get_wildcard_redirect_map("grid.smsly.cloud"), {})
