"""HTTP-proof verification fallback (orange-compatible custom domains).

DNS-quorum cannot see through Cloudflare proxying or apex CNAME
flattening (edge IPs / NoAnswer). The fallback fetches the row's token
over the public edge instead. Tests pin: token creation, direct proof
outcomes, fallback integration inside verify_custom_domain_dns, and the
public challenge endpoint (200 only on exact token).
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase

from apps.domains.verification import (
    ensure_verification_token,
    verify_custom_domain_dns,
    verify_http_proof,
)


def _domain(name="shop.example.com", token="tok-abc-123"):
    d = mock.MagicMock()
    d.domain_name = name
    d.verification_token = token
    return d


def _config(server_ip="176.31.201.181", domain="grid.smsly.cloud"):
    cfg = mock.MagicMock()
    cfg.server_ip = server_ip
    cfg.domain = domain
    return cfg


def _resp(status=200, body="tok-abc-123"):
    m = mock.MagicMock()
    m.status_code = status
    m.text = body
    return m


class EnsureTokenTests(SimpleTestCase):
    def test_blank_token_created_and_saved(self):
        d = _domain(token="")
        token = ensure_verification_token(d)
        self.assertGreaterEqual(len(token), 43)
        d.save.assert_called_once_with(update_fields=["verification_token"])

    def test_existing_token_kept_without_save(self):
        d = _domain(token="keep-me")
        self.assertEqual(ensure_verification_token(d), "keep-me")
        d.save.assert_not_called()


class HttpProofTests(SimpleTestCase):
    @mock.patch("requests.get")
    def test_matching_token_verifies(self, mock_get):
        mock_get.return_value = _resp(200, "tok-abc-123")
        ok, detail = verify_http_proof(_domain())
        self.assertTrue(ok)
        mock_get.assert_called_once()
        self.assertIn("smsly-verify/tok-abc-123", mock_get.call_args[0][0])

    @mock.patch("requests.get")
    def test_wrong_body_fails(self, mock_get):
        mock_get.return_value = _resp(200, "something-else")
        ok, detail = verify_http_proof(_domain())
        self.assertFalse(ok)
        self.assertIn("HTTP 200", detail)

    @mock.patch("requests.get")
    def test_fetch_error_fails_open(self, mock_get):
        mock_get.side_effect = OSError("no route")
        ok, detail = verify_http_proof(_domain())
        self.assertFalse(ok)
        self.assertIn("fetch failed", detail)

    def test_missing_token_skips(self):
        ok, detail = verify_http_proof(_domain(token=""))
        self.assertFalse(ok)
        self.assertEqual(detail, "no token")


class FallbackIntegrationTests(SimpleTestCase):
    def _empty_dns(self):
        return mock.patch(
            "apps.domains.verification._resolve_rrset", return_value=[])

    @mock.patch("requests.get")
    def test_dns_blind_but_http_proof_verifies(self, mock_get):
        mock_get.return_value = _resp(200, "tok-abc-123")
        with self._empty_dns():
            result = verify_custom_domain_dns(_domain(), _config())
        self.assertTrue(result.verified)
        self.assertIn("HTTP proof", result.matched_by)

    @mock.patch("requests.get")
    def test_both_blind_stays_unverified(self, mock_get):
        mock_get.return_value = _resp(404, "not found")
        with self._empty_dns():
            result = verify_custom_domain_dns(_domain(), _config())
        self.assertFalse(result.verified)
        self.assertIn("HTTP proof", result.error)


class ChallengeEndpointTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from apps.cloud.models import CloudProvider
        from apps.deployments.models import Service
        from apps.domains.models import Domain
        user = get_user_model().objects.create_user(
            username="proof-user", email="proof@example.com",
            password="password123")
        provider = CloudProvider.objects.create(
            name="proof-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True)
        service = Service.objects.create(
            name="proof-service", owner=user, provider=provider,
            public_domain="proof-service.cloud.smsly.cloud")
        Domain.objects.create(
            domain_name="shop.example.com", service=service,
            verification_token="tok-abc-123")

    def test_exact_token_served_as_text(self):
        from django.test import Client
        resp = Client().get("/.well-known/smsly-verify/tok-abc-123/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode(), "tok-abc-123")
        self.assertIn("text/plain", resp["Content-Type"])

    def test_unknown_token_404s(self):
        from django.test import Client
        resp = Client().get("/.well-known/smsly-verify/nope-not-real/")
        self.assertEqual(resp.status_code, 404)
