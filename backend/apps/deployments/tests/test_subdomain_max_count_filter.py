# pylint: disable=invalid-name
"""Tests for the subdomain MAX-count filter (Issue 32).

The original ``subdomains_list_create`` view used
``ReservedSubdomain.objects.filter(owner=user, is_active=True)``
to count active reservations.  The fix adds
``released_at__isnull=True`` so a soft-deleted row
(``is_active=True`` but ``released_at`` set, e.g. by a buggy
import path) is still excluded from the count.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.deployments.models.tunnels import ReservedSubdomain

User = get_user_model()


class SubdomainMaxCountFilterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sub-count-user", password="x",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = "/api/v1/subdomains/"

    def _reserve(self, subdomain):
        return self.client.post(
            self.url, {"subdomain": subdomain}, format="json",
        )

    def test_active_reservation_blocks_new_reservation(self):
        for i in range(5):
            self._reserve(f"sub{i}")

        resp = self._reserve("fresh-2")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Maximum", str(resp.data.get("error", "")))

    def test_count_excludes_released_reservation(self):
        """A row with ``released_at`` set is excluded from the MAX
        count even when ``is_active`` is also True (e.g. buggy
        import).  The new filter is ``is_active=True AND
        released_at__isnull=True``."""
        for i in range(5):
            self._reserve(f"sub{i}")

        ReservedSubdomain.objects.filter(subdomain="sub0").update(
            is_active=True, released_at=timezone.now(),
        )

        resp = self._reserve("fresh-1")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            ReservedSubdomain.objects.filter(
                owner=self.user,
                is_active=True,
                released_at__isnull=True,
            ).count(),
            5,
        )

    def test_count_drops_after_release(self):
        for i in range(5):
            self._reserve(f"sub{i}")

        ReservedSubdomain.objects.filter(subdomain="sub0").update(
            is_active=False, released_at=timezone.now(),
        )

        resp = self._reserve("fresh-3")
        self.assertIn(resp.status_code, (201, 429))

    def test_count_includes_only_owned_reservations(self):
        other = User.objects.create_user(
            username="sub-count-other", password="x",
        )
        for i in range(5):
            self._reserve(f"sub{i}")

        other_client = APIClient()
        other_client.force_authenticate(user=other)
        resp = other_client.post(
            self.url, {"subdomain": "other-1"}, format="json",
        )
        self.assertEqual(resp.status_code, 201)
