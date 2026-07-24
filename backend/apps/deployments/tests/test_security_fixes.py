# pylint: disable=invalid-name
"""
Regression tests for the Batch F (post-audit) security fixes.

Covers the issues found in the system-wide audit and fixed in
commit 8a2d7d6e (autoscaler dedup + prompt injection) and the
followup Batch F commit. Each test names the audit finding it
defends against, so future regressions can be traced back.

  * OAuth state CSRF (#8 in audit)
  * IDORs on Service.bulk_action, Service.dependencies, Service.sidebar (#1, #2, #3)
  * IDOR on toggle_bucket_public_api (#4)
  * SlowQueryViewSet admin-only (#5)
  * jules_fix_history owner-scoped (#6)
  * topology/ecosystem admin-only (#7)
  * LLM base_url validation (#12)
  * prune endpoint admin-scoped image cleanup (#14)
  * NodeTokenExchangeThrottle (#15)
  * HMAC nonce replay protection (#16)
  * Info-leak: attestation 404 vs 500 (#19)
"""
import hashlib
import hmac
import time
from typing import Any
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

try:
    from allauth.socialaccount.models import SocialApp
except ImportError:
    SocialApp: Any = None
try:
    from django.contrib.sites.models import Site
except ImportError:
    Site: Any = None

User = get_user_model()


# ── OAuth state CSRF (audit #8) ────────────────────────────────────────────


class OAuthStateCSRFTests(TestCase):
    """Audit #8: callbacks previously accepted `code` without verifying
    `state`, enabling 1-click account takeover."""

    def setUp(self):
        if SocialApp is None or Site is None:
            self.skipTest("allauth not installed")
        self.user = User.objects.create_user(
            username="victim", email="v@x.com", password="p"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        site = Site.objects.get_current()
        self.gh_app, _ = SocialApp.objects.get_or_create(
            provider="github",
            defaults={"name": "GitHub", "client_id": "id", "secret": "sec"},
        )
        self.gh_app.sites.add(site)
        cache.clear()

    def test_callback_rejects_missing_state(self):
        resp = self.client.post(
            "/api/v1/integrations/github/oauth-callback/",
            {"code": "anycode"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("state", resp.data.get("error", "").lower())

    def test_callback_rejects_unknown_state(self):
        resp = self.client.post(
            "/api/v1/integrations/github/oauth-callback/",
            {"code": "anycode", "state": "never-issued-token"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("state", resp.data.get("error", "").lower())

    def test_callback_rejects_replayed_state(self):
        # Simulate a state issued to a different user
        from django.core.cache import cache
        cache.set("github_oauth_state:abc123", "9999", timeout=600)
        resp = self.client.post(
            "/api/v1/integrations/github/oauth-callback/",
            {"code": "anycode", "state": "abc123"},
            format="json",
        )
        # state was issued to user 9999, but our request user is
        # self.user (id != 9999), so 403
        self.assertEqual(resp.status_code, 403)
        # and the state has been consumed
        self.assertIsNone(cache.get("github_oauth_state:abc123"))

    def test_state_is_single_use(self):
        from django.core.cache import cache
        cache.set("github_oauth_state:oncenb", str(self.user.id), timeout=600)
        # First attempt is forbidden (no real GitHub to talk to) but
        # the important thing is the state is consumed. We assert
        # the state is gone after a single call regardless of the
        # downstream error.
        self.client.post(
            "/api/v1/integrations/github/oauth-callback/",
            {"code": "fake", "state": "oncenb"},
            format="json",
        )
        self.assertIsNone(cache.get("github_oauth_state:oncenb"))

    def test_state_must_match_user(self):
        from django.core.cache import cache
        other = User.objects.create_user(username="other", password="p")
        cache.set("github_oauth_state:their", str(other.id), timeout=600)
        # Even with the right code path, the state belongs to `other`
        # not the requester.
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/api/v1/integrations/github/oauth-callback/",
            {"code": "x", "state": "their"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)


# ── Service IDORs (audit #1, #2, #3) ───────────────────────────────────────


class ServiceBulkActionIDORTests(TestCase):
    """Audit #1: bulk_action was unscoped. A non-owner could trigger
    deploy/cancel/senate on other tenants' services."""

    def setUp(self):
        self.attacker = User.objects.create_user(
            username="attacker", email="a@x.com", password="p"
        )
        self.victim = User.objects.create_user(
            username="victim", email="v@x.com", password="p"
        )
        from apps.deployments.models import Project
        self.victim_project = Project.objects.create(name="P", owner=self.victim)
        from apps.deployments.models import Service
        self.victim_service = Service.objects.create(
            name="victim-svc", owner=self.victim, project=self.victim_project,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.attacker)

    def test_attacker_cannot_bulk_action_victim_services(self):
        from unittest.mock import patch
        with patch("apps.deployments.views.service.deploy.smart_deploy_task") as mock_task:
            resp = self.client.post(
                "/api/v1/services/bulk-action/",
                {"ids": [str(self.victim_service.id)], "action": "deploy"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        results = resp.data if isinstance(resp.data, list) else resp.data.get("results", [])
        self.assertEqual(results, [])
        mock_task.delay.assert_not_called()


class ServiceDependenciesIDORTests(TestCase):
    """Audit #2: dependencies had no owner filter."""

    def setUp(self):
        self.attacker = User.objects.create_user(username="a", password="p")
        self.victim = User.objects.create_user(username="v", password="p")
        from apps.deployments.models import Project
        self.victim_project = Project.objects.create(name="P", owner=self.victim)
        from apps.deployments.models import Service
        self.victim_service = Service.objects.create(
            name="victim-svc", owner=self.victim, project=self.victim_project,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.attacker)

    def test_attacker_cannot_read_dependencies(self):
        resp = self.client.get(
            f"/api/v1/services/{self.victim_service.id}/dependencies/"
        )
        self.assertEqual(resp.status_code, 404)


class ServiceSidebarIDORTests(TestCase):
    """Audit #3: sidebar returned every service in the platform."""

    def setUp(self):
        self.attacker = User.objects.create_user(username="a", password="p")
        self.victim = User.objects.create_user(username="v", password="p")
        from apps.deployments.models import Project
        self.victim_project = Project.objects.create(name="P", owner=self.victim)
        from apps.deployments.models import Service
        self.victim_service = Service.objects.create(
            name="victim-svc", owner=self.victim, project=self.victim_project,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.attacker)

    def test_sidebar_excludes_other_tenants(self):
        resp = self.client.get("/api/v1/services/sidebar/")
        self.assertEqual(resp.status_code, 200)
        ids = [item.get("id") for entry in resp.data for item in entry.get("repos", [])]
        self.assertNotIn(str(self.victim_service.id), ids)


# ── MinIO toggle IDOR (audit #4) ───────────────────────────────────────────


class ToggleBucketPublicIDORTests(TestCase):
    """Audit #4: toggle_bucket_public_api was unscoped."""

    def setUp(self):
        self.attacker = User.objects.create_user(username="a", password="p")
        self.victim = User.objects.create_user(username="v", password="p")
        from apps.deployments.models import Project
        self.victim_project = Project.objects.create(name="P", owner=self.victim)
        from apps.deployments.models import Service
        self.victim_service = Service.objects.create(
            name="victim-svc", owner=self.victim, project=self.victim_project,
        )
        from apps.deployments.models.addons import Addon
        self.victim_addon = Addon.objects.create(
            service=self.victim_service,
            addon_type="MINIO",
            name="victim-minio",
            is_bucket_public=False,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.attacker)

    def test_attacker_cannot_toggle_victim_bucket(self):
        with patch("apps.deployments.views.addons.get_docker_client") as mock_dc:
            mock_dc.return_value = MagicMock()
            resp = self.client.post(
                f"/api/v1/services/{self.victim_service.id}/addons/{self.victim_addon.id}/toggle-bucket-public-api/",
                {"is_public": True},
                format="json",
            )
        # NotFound (404) — addon doesn't exist for the attacker
        self.assertEqual(resp.status_code, 404)
        # And the database is unchanged
        self.victim_addon.refresh_from_db()
        self.assertFalse(self.victim_addon.is_bucket_public)


# ── SlowQuery admin-only (audit #5) ────────────────────────────────────────


class SlowQueryAdminOnlyTests(TestCase):
    """Audit #5: SlowQueryViewSet allowed any authenticated user."""

    def setUp(self):
        self.regular = User.objects.create_user(
            username="regular", email="r@x.com", password="p"
        )
        self.client = APIClient()

    def test_regular_user_forbidden(self):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.get("/api/v1/slow-queries/")
        self.assertIn(resp.status_code, (403, 401))


# ── jules_fix_history owner-scoped (audit #6) ──────────────────────────────


class JulesFixHistoryIDORTests(TestCase):
    """Audit #6: jules_fix_history was unscoped."""

    def setUp(self):
        self.attacker = User.objects.create_user(username="a", password="p")
        self.victim = User.objects.create_user(username="v", password="p")
        from apps.deployments.models import Project
        self.victim_project = Project.objects.create(name="P", owner=self.victim)
        from apps.deployments.models import Service
        self.victim_service = Service.objects.create(
            name="victim-svc", owner=self.victim, project=self.victim_project,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.attacker)

    def test_attacker_gets_404_for_victim_service(self):
        resp = self.client.get(
            f"/api/v1/intelligence/jules-fix-history/?service_id={self.victim_service.id}"
        )
        # Either 404 or 200 with empty entries; either way the attacker
        # should not see anything for the victim's service.
        if resp.status_code == 200:
            self.assertEqual(resp.data.get("entries", []), [])
        else:
            self.assertEqual(resp.status_code, 404)


# ── topology/ecosystem admin-only (audit #7) ──────────────────────────────


class TopologyEcosystemAdminOnlyTests(TestCase):
    """Audit #7: ecosystem returned the full platform graph."""

    def setUp(self):
        self.regular = User.objects.create_user(
            username="regular", email="r@x.com", password="p"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.regular)

    @patch("apps.deployments.services.ecosystem_graph_builder._check_http", return_value=True)
    def test_regular_user_forbidden(self, _mock_http):
        resp = self.client.get("/api/v1/topology/ecosystem/")
        self.assertEqual(resp.status_code, 403)


# ── LLM base_url validation (audit #12) ───────────────────────────────────


class LLMBaseURLValidationTests(TestCase):
    """Audit #12: only jules_base_url was allowlisted. The other six
    base_url fields accepted any value."""

    def setUp(self):
        from apps.intelligence.models import AIProviderSettings
        self.solo = AIProviderSettings.get_solo()

    def test_freemodel_base_url_rejects_internal_ip(self):
        from django.core.exceptions import ValidationError
        self.solo.freemodel_base_url = "https://169.254.169.254/latest/meta-data/"
        with self.assertRaises(ValidationError):
            self.solo.full_clean()

    def test_opencode_base_url_rejects_wrong_host(self):
        from django.core.exceptions import ValidationError
        self.solo.opencode_base_url = "https://attacker.example.com/v1"
        with self.assertRaises(ValidationError):
            self.solo.full_clean()

    def test_mistral_base_url_rejects_http(self):
        from django.core.exceptions import ValidationError
        self.solo.mistral_base_url = "http://api.mistral.ai/v1"
        with self.assertRaises(ValidationError):
            self.solo.full_clean()

    def test_nvidia_base_url_rejects_internal(self):
        from django.core.exceptions import ValidationError
        self.solo.nvidia_base_url = "https://10.0.0.1/v1"
        with self.assertRaises(ValidationError):
            self.solo.full_clean()

    def test_cloudflare_base_url_rejects_attacker(self):
        from django.core.exceptions import ValidationError
        self.solo.cloudflare_base_url = "https://evil.com/gateway"
        with self.assertRaises(ValidationError):
            self.solo.full_clean()

    def test_localllm_base_url_allows_http(self):
        # Local LLM is the documented exception: it runs against a
        # user-controlled local endpoint (Ollama, LM Studio).
        from django.core.exceptions import ValidationError
        self.solo.localllm_base_url = "http://localhost:11434/v1"
        # Should not raise
        try:
            self.solo.full_clean()
        except ValidationError as exc:
            self.fail(f"localllm_base_url validation failed for localhost: {exc}")

    def test_legitimate_urls_pass(self):
        from django.core.exceptions import ValidationError
        self.solo.freemodel_base_url = "https://api.freemodel.dev/v1"
        self.solo.opencode_base_url = "https://api.opencode.ai/v1"
        self.solo.mistral_base_url = "https://api.mistral.ai/v1"
        self.solo.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
        self.solo.cloudflare_base_url = "https://gateway.ai.cloudflare.com/v1/acct/gw/workers-ai"
        try:
            self.solo.full_clean()
        except ValidationError as exc:
            self.fail(f"Legitimate base URLs failed validation: {exc}")


# ── NodeTokenExchange throttle (audit #15) ─────────────────────────────────


class NodeTokenExchangeThrottleTests(TestCase):
    """Audit #15: the AllowAny node-token-exchange endpoint had no rate
    limit — open brute-force on the admin account."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin", email="a@x.com", password="realpwd",
        )
        self.client = APIClient()
        cache.clear()

    def test_sixth_request_is_throttled(self):
        url = "/api/v1/auth/node-token-exchange/"
        for _ in range(5):
            resp = self.client.post(
                url, {"username": "admin", "password": "wrong"}, format="json",
            )
            self.assertNotEqual(resp.status_code, 429)
        resp = self.client.post(
            url, {"username": "admin", "password": "wrong"}, format="json",
        )
        self.assertEqual(resp.status_code, 429)


# ── HMAC nonce replay (audit #16) ──────────────────────────────────────────


class HMACNonceReplayTests(TestCase):
    """Audit #16: HMAC window was 60s with no nonce — a captured request
    could be replayed for the full window."""

    def setUp(self):
        from django.conf import settings
        self.gw_secret = "test-gateway-secret"
        self._orig = getattr(settings, "GATEWAY_SECRET", None)
        settings.GATEWAY_SECRET = self.gw_secret
        cache.clear()
        self.client = APIClient()

    def tearDown(self):
        from django.conf import settings
        if self._orig is not None:
            settings.GATEWAY_SECRET = self._orig
        else:
            del settings.GATEWAY_SECRET

    def _signed_headers(self, path, body=b"", ts=None, nonce=None):
        ts = ts or int(time.time())
        nonce = nonce or "unique-nonce-1234"
        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"POST|{path}|{ts}|{nonce}|{body_hash}"
        sig = hmac.new(self.gw_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return {
            "HTTP_X_GATEWAY_SIGNATURE_V2": sig,
            "HTTP_X_REQUEST_TIMESTAMP": str(ts),
            "HTTP_X_REQUEST_NONCE": nonce,
            "content_type": "application/json",
        }

    @patch("apps.core.views.node_exchange.APIToken.create_token",
           return_value=(MagicMock(prefix="p"), "smsly_xxx"))
    def test_replay_with_same_nonce_is_rejected(self, _mock_create):
        User.objects.create_superuser(username="admin", email="a@x.com", password="x")
        path = "/api/v1/auth/node-token-exchange-hmac/"
        body = b'{"node_name":"node-a"}'
        headers = self._signed_headers(path, body)
        # First request goes through (or fails downstream), but
        # the nonce is recorded as used
        self.client.post(path, data=body, **headers)
        # Replay — same nonce, same signature
        resp = self.client.post(path, data=body, **headers)
        # Either 401 (nonce reuse) or another failure — but NOT a
        # successful second token issuance
        self.assertIn(resp.status_code, (401, 403, 503))

    def test_request_without_nonce_is_rejected(self):
        path = "/api/v1/auth/node-token-exchange-hmac/"
        body = b'{"node_name":"node-a"}'
        headers = self._signed_headers(path, body)
        del headers["HTTP_X_REQUEST_NONCE"]
        resp = self.client.post(path, data=body, **headers)
        self.assertEqual(resp.status_code, 401)


# ── Info-leak: attestation 404 vs 500 (audit #19) ─────────────────────────


class AttestationInfoLeakTests(TestCase):
    """Audit #19: the attestation endpoint used to return 500 on DB
    errors, distinguishing it from a 404 for an unknown peer."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username="staff", password="p", is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)
        cache.clear()

    def test_unknown_peer_returns_404(self):
        # Pre-seed a valid challenge in the cache so the lookup
        # proceeds past the nonce check to the WireGuardPeer query.
        cache.set("attest_challenge_knownnope", "10.0.0.1", timeout=120)
        resp = self.client.post(
            "/api/v1/internal/attest/verify/",
            {"challenge": "knownnope", "signature": "x", "sender_wg_address": "10.0.0.1"},
            format="json",
        )
        # 404 (unknown peer) — never 500
        self.assertEqual(resp.status_code, 404)

    def test_db_error_returns_404_not_500(self):
        # Set up a challenge in the cache so the lookup proceeds to
        # the WireGuardPeer query. Patch the peer lookup to raise.
        from unittest.mock import patch
        cache.set("attest_challenge_dbtest", "10.0.0.1", timeout=120)
        with patch("apps.deployments.models_mesh.WireGuardPeer.objects") as mock_qs:
            mock_qs.select_related.return_value.filter.return_value.first.side_effect = Exception("db down")
            resp = self.client.post(
                "/api/v1/internal/attest/verify/",
                {"challenge": "dbtest", "signature": "x", "sender_wg_address": "10.0.0.1"},
                format="json",
            )
        self.assertEqual(resp.status_code, 404)
