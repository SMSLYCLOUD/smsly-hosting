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
        "LOCATION": "smtp-key-test",
    }
}


@override_settings(CACHES=TEST_CACHES)
class Finding79SmtpFailureKeyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="adm79", password="p", email="adm79@example.com",
        )
        self.invitee = User.objects.create_user(
            username="invitee79", password="p", email="invitee79@example.com",
        )
        self.team = Team.objects.create(name="team79", owner=self.admin)
        TeamMember.objects.create(
            team=self.team, user=self.admin, role=TeamMember.Role.ADMIN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.url = f"/api/v1/teams/{self.team.id}/invite_member/"

    def test_response_error_key_is_literally_smtp_failure(self):
        with patch(
            "apps.teams.views.send_mail",
            side_effect=SMTPException("SMTP 451 throttled"),
        ):
            resp = self.client.post(
                self.url,
                data={"email": self.invitee.email, "role": "MEMBER"},
                format="json",
            )
        self.assertEqual(resp.status_code, http_status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(resp.data["error"], "smtp_failure")
        self.assertIn("mail_error", resp.data)
        self.assertIn("SMTP 451", resp.data["mail_error"])
        self.assertEqual(resp.data["mail_error"], "SMTP 451 throttled")

    def test_fail_silently_disabled_when_smtp_call_made(self):
        with patch("apps.teams.views.send_mail") as send_mail_mock:
            self.client.post(
                self.url,
                data={"email": self.invitee.email, "role": "MEMBER"},
                format="json",
            )
        send_mail_mock.assert_called_once()
        self.assertEqual(
            send_mail_mock.call_args.kwargs.get("fail_silently"),
            False,
        )

    def test_member_created_even_when_smtp_fails(self):
        with patch(
            "apps.teams.views.send_mail",
            side_effect=SMTPException("relay refused"),
        ):
            self.client.post(
                self.url,
                data={"email": self.invitee.email, "role": "VIEWER"},
                format="json",
            )
        self.assertTrue(
            TeamMember.objects.filter(
                team=self.team, user=self.invitee,
            ).exists()
        )

    def test_smtp_exception_message_propagated_to_response(self):
        secret_detail = (
            "454 4.7.1 <attacker@example.com>: Relay access denied"
        )
        with patch(
            "apps.teams.views.send_mail",
            side_effect=SMTPException(secret_detail),
        ):
            resp = self.client.post(
                self.url,
                data={"email": self.invitee.email, "role": "MEMBER"},
                format="json",
            )
        self.assertEqual(resp.status_code, http_status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(resp.data["error"], "smtp_failure")
        self.assertIn("mail_error", resp.data)
        self.assertEqual(resp.data["mail_error"], secret_detail)
