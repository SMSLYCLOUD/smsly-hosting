# pylint: disable=invalid-name
"""Tests for the ``Team.updated_at`` field (Issue 144).

The Team model previously only tracked ``created_at``. Every save
should now refresh ``updated_at`` automatically so audit and UI
flows can sort by recency.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.teams.models import Team

User = get_user_model()


class TeamUpdatedAtTests(TestCase):
    def test_team_has_updated_at_field(self):
        field = Team._meta.get_field("updated_at")
        self.assertIsNotNone(field)
        self.assertTrue(field.auto_now)

    def test_team_save_updates_updated_at(self):
        owner = User.objects.create_user(username="u144", password="x")
        team = Team.objects.create(name="t1", owner=owner)
        first = team.updated_at
        self.assertIsNotNone(first)

        team.name = "t1-renamed"
        team.save()
        team.refresh_from_db()
        self.assertGreaterEqual(team.updated_at, first)

    def test_team_updated_at_changes_between_saves(self):
        owner = User.objects.create_user(username="u144b", password="x")
        team = Team.objects.create(name="t2", owner=owner)
        original = team.updated_at
        team.name = "t2b"
        team.save()
        team.refresh_from_db()
        self.assertNotEqual(team.updated_at, original)
