"""
Regression tests for the subdomain release-cooldown fix (Issue 32).

Covers:
  1. A user that released a subdomain cannot re-claim it within the
     cooldown window.
  2. After the cooldown elapses, the same user can re-claim the
     subdomain.
  3. A different user is not blocked by another user's release
     cooldown.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.deployments.models.tunnels import ReservedSubdomain

User = get_user_model()


class SubdomainCooldownTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="cooldown-user", password="p",
        )
        self.other = User.objects.create_user(
            username="cooldown-other", password="p",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = "/api/v1/subdomains/"

    def _reserve(self, subdomain):
        resp = self.client.post(
            self.url, {"subdomain": subdomain}, format="json",
        )
        return resp

    def _release(self, subdomain):
        resp = self.client.delete(f"/api/v1/subdomains/{subdomain}/")
        return resp

    def test_re_reserve_within_cooldown_returns_429(self):
        self._reserve("alpha")
        self._release("alpha")

        resp = self._reserve("alpha")
        self.assertEqual(resp.status_code, 429)
        self.assertIn("cooldown", str(resp.data).lower())

    def test_re_reserve_after_cooldown_succeeds(self):
        self._reserve("beta")
        self._release("beta")

        # Backdate the release so the cooldown has elapsed.
        ReservedSubdomain.objects.filter(subdomain="beta").update(
            released_at=timezone.now() - timedelta(hours=25),
        )

        resp = self._reserve("beta")
        self.assertEqual(resp.status_code, 201)

    def test_other_user_unaffected_by_first_users_cooldown(self):
        # First user reserves and releases.
        self._reserve("gamma")
        self._release("gamma")

        # A different user can still claim the same subdomain right away
        # because the cooldown is per-user, not global.
        other_client = APIClient()
        other_client.force_authenticate(user=self.other)
        resp = other_client.post(
            self.url, {"subdomain": "gamma"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_first_user_still_blocked_when_other_holds_the_subdomain(self):
        self._reserve("delta")
        self._release("delta")

        # Other user takes the freed subdomain during the cooldown.
        other_client = APIClient()
        other_client.force_authenticate(user=self.other)
        ok = other_client.post(
            self.url, {"subdomain": "delta"}, format="json",
        )
        self.assertEqual(ok.status_code, 201)

        # The original user is blocked: the active reservation now held by
        # another user takes precedence (409 conflict) over the cooldown
        # check (429). Either way the request is rejected.
        resp = self._reserve("delta")
        self.assertIn(resp.status_code, (409, 429))
