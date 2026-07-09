"""
Regression tests for Issue 80.

The ``remove_member`` action must refuse to remove the last
ADMIN of a team. Lock in the existing behavior with a test so a
future refactor can't silently regress it.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework import status as http_status
from rest_framework.test import APIClient

from apps.teams.models import Team, TeamMember

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "last-admin-test",
    }
}


@override_settings(CACHES=TEST_CACHES)
class CannotRemoveLastAdminTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="team-owner", password="p",
        )
        self.admin_user = User.objects.create_user(
            username="solo-admin", password="p",
        )
        self.team = Team.objects.create(name="t", owner=self.owner)
        TeamMember.objects.create(
            team=self.team,
            user=self.admin_user,
            role=TeamMember.Role.ADMIN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin_user)

    def test_cannot_remove_last_admin(self):
        url = f"/api/v1/teams/{self.team.id}/remove_member/"
        resp = self.client.post(
            url,
            data={"user_id": self.admin_user.id},
            format="json",
        )
        self.assertEqual(resp.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn("last admin", (resp.data.get("error") or "").lower())
        # The membership was not deleted.
        self.assertTrue(
            TeamMember.objects.filter(
                team=self.team, user=self.admin_user, role="ADMIN",
            ).exists()
        )

    def test_admin_removal_allowed_when_a_second_admin_exists(self):
        second = User.objects.create_user(
            username="second-admin", password="p",
        )
        TeamMember.objects.create(
            team=self.team, user=second, role=TeamMember.Role.ADMIN,
        )
        url = f"/api/v1/teams/{self.team.id}/remove_member/"
        resp = self.client.post(
            url,
            data={"user_id": second.id},
            format="json",
        )
        self.assertEqual(resp.status_code, http_status.HTTP_200_OK)
        self.assertFalse(
            TeamMember.objects.filter(team=self.team, user=second).exists()
        )
