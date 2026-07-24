# pylint: disable=invalid-name
"""Tests for Issue 92: ManagedServer.get_queryset team membership filter."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Project
from apps.deployments.models.servers import ManagedServer
from apps.teams.models import Team, TeamMember

User = get_user_model()


class ManagedServerTeamMembershipTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="srv-owner", password="p",
        )
        self.team_member = User.objects.create_user(
            username="srv-member", password="p",
        )
        self.outsider = User.objects.create_user(
            username="srv-outsider", password="p",
        )
        self.team = Team.objects.create(name="srv-team", owner=self.owner)
        TeamMember.objects.create(
            team=self.team, user=self.team_member, role=TeamMember.Role.MEMBER,
        )
        self.project = Project.objects.create(
            name="srv-project", owner=self.owner, team=self.team,
        )
        self.owned_server = ManagedServer.objects.create(
            owner=self.owner,
            name="owned",
            host="10.0.0.1",
            api_url="https://owned.example.com",
            api_token="tok",
        )
        self.shared_server = ManagedServer.objects.create(
            owner=self.owner,
            name="shared",
            host="10.0.0.2",
            api_url="https://shared.example.com",
            api_token="tok",
        )
        self.shared_server.project = self.project
        self.shared_server.save(update_fields=["project"])
        self.outsider_server = ManagedServer.objects.create(
            owner=self.outsider,
            name="outsider",
            host="10.0.0.3",
            api_url="https://outsider.example.com",
            api_token="tok",
        )

    def test_owner_sees_both_owned_and_team_shared_servers(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        resp = client.get("/api/v1/servers/")
        names = self._names(resp.data)
        self.assertIn("owned", names)
        self.assertIn("shared", names)

    def test_team_member_sees_team_shared_server(self):
        client = APIClient()
        client.force_authenticate(user=self.team_member)
        resp = client.get("/api/v1/servers/")
        names = self._names(resp.data)
        self.assertIn("shared", names)
        self.assertNotIn("owned", names)
        self.assertNotIn("outsider", names)

    def test_outsider_sees_no_servers(self):
        client = APIClient()
        client.force_authenticate(user=self.outsider)
        resp = client.get("/api/v1/servers/")
        names = self._names(resp.data)
        self.assertNotIn("owned", names)
        self.assertNotIn("shared", names)
        self.assertIn("outsider", names)

    def _names(self, data):
        items = data if isinstance(data, list) else data.get("results", data)
        return {item["name"] for item in items}
