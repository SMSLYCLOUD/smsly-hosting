"""
Regression tests for Finding #125 (info disclosure via invite_member).

``invite_member`` used to return ``404 Not Found`` when the
invitee email was not associated with an existing user. That
leaked account existence to any admin (a tenant could
enumerate which emails are registered). The fix returns
``403 Forbidden`` with a generic body so the caller cannot
distinguish ``not found`` from ``forbidden``.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status as http_status
from rest_framework.test import APIClient

from apps.teams.models import Team, TeamMember


class Finding125InviteMemberInfoDisclosureTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin-125", password="p", email="admin125@example.com",
        )
        self.team = Team.objects.create(name="t-125", owner=self.admin)
        TeamMember.objects.create(
            team=self.team, user=self.admin, role=TeamMember.Role.ADMIN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.url = f"/api/v1/teams/{self.team.id}/invite_member/"

    def test_unknown_email_returns_403_not_404(self):
        with patch("apps.teams.views.send_mail") as _mail:
            resp = self.client.post(
                self.url,
                data={"email": "ghost-125@example.com", "role": "MEMBER"},
                format="json",
            )
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)
        self.assertNotEqual(resp.status_code, http_status.HTTP_404_NOT_FOUND)
        self.assertEqual(resp.data, {"error": "Forbidden"})
        _mail.assert_not_called()

    def test_existing_user_invite_still_succeeds(self):
        invitee = User.objects.create_user(
            username="invitee-125", password="p", email="invitee125@example.com",
        )
        with patch("apps.teams.views.send_mail") as _mail:
            resp = self.client.post(
                self.url,
                data={"email": invitee.email, "role": "MEMBER"},
                format="json",
            )
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
        self.assertTrue(
            TeamMember.objects.filter(
                team=self.team, user=invitee,
            ).exists()
        )

    def test_status_code_does_not_leak_membership_state(self):
        """A non-admin trying to invite should see 403 too, so the
        response shape is uniform for ``Forbidden`` regardless of
        the underlying reason (admin-only, unknown email, etc.)."""
        non_admin = User.objects.create_user(
            username="member-125", password="p", email="member125@example.com",
        )
        TeamMember.objects.create(
            team=self.team, user=non_admin, role=TeamMember.Role.MEMBER,
        )
        self.client.force_authenticate(user=non_admin)
        resp = self.client.post(
            self.url,
            data={"email": "ghost-125@example.com", "role": "MEMBER"},
            format="json",
        )
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)
