"""
Regression tests for Issue 79.

``invite_member`` must not silently swallow SMTP failures. The
admin must learn that the invitee never received the email so they
can retry, fix the SMTP config, or contact the invitee out-of-band.
"""
from smtplib import SMTPException
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework import status as http_status
from rest_framework.test import APIClient

from apps.teams.models import Team, TeamMember

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "smtp-test",
    }
}


@override_settings(CACHES=TEST_CACHES)
class InviteMemberSmtpFailureTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", password="p", email="admin@example.com",
        )
        self.invitee = User.objects.create_user(
            username="invitee", password="p", email="invitee@example.com",
        )
        self.team = Team.objects.create(name="t", owner=self.admin)
        TeamMember.objects.create(
            team=self.team, user=self.admin, role=TeamMember.Role.ADMIN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.url = f"/api/v1/teams/{self.team.id}/invite_member/"

    def test_smtp_failure_returns_502_with_mail_error(self):
        with patch(
            "apps.teams.views.send_mail",
            side_effect=SMTPException("SMTP 451 relay not permitted"),
        ):
            resp = self.client.post(
                self.url,
                data={"email": self.invitee.email, "role": "MEMBER"},
                format="json",
            )
        self.assertEqual(resp.status_code, http_status.HTTP_502_BAD_GATEWAY)
        self.assertIn("mail_error", resp.data)
        self.assertIn("SMTP 451", resp.data["mail_error"])
        self.assertEqual(resp.data["mail_error"], "SMTP 451 relay not permitted")
        # The membership was created before the email attempt —
        # the admin can retry once SMTP is healthy.
        self.assertTrue(
            TeamMember.objects.filter(
                team=self.team, user=self.invitee,
            ).exists()
        )

    def test_smtp_success_returns_200(self):
        with patch("apps.teams.views.send_mail") as send_mail_mock:
            resp = self.client.post(
                self.url,
                data={"email": self.invitee.email, "role": "MEMBER"},
                format="json",
            )
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
        send_mail_mock.assert_called_once()
        kwargs = send_mail_mock.call_args.kwargs
        self.assertFalse(kwargs.get("fail_silently", True))
