from django.test import SimpleTestCase

from apps.deployments.services.caddy_manager.config_generation import (
    _www_apex_redirect_target,
)
from apps.deployments.services.caddy_manager.validation import (
    validate_wildcard_redirects_authoritative,
)


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
