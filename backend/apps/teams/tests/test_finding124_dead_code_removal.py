"""
Regression tests for Finding #124 (dead code).

``IsTeamAdminOrMember`` was an unused permission class in
``apps/teams/permissions.py``. The class has been removed; the
``IsTeamMember`` and ``IsTeamAdmin`` classes continue to work and
``teams/permissions`` still imports cleanly.
"""
import importlib

from django.test import SimpleTestCase

from apps.teams import permissions as teams_permissions


class Finding124DeadCodeRemovalTests(SimpleTestCase):
    def test_is_team_admin_or_member_class_is_removed(self):
        self.assertFalse(hasattr(teams_permissions, "IsTeamAdminOrMember"))

    def test_remaining_permission_classes_still_present(self):
        self.assertTrue(hasattr(teams_permissions, "IsTeamMember"))
        self.assertTrue(hasattr(teams_permissions, "IsTeamAdmin"))

    def test_permissions_module_imports_cleanly(self):
        module = importlib.import_module("apps.teams.permissions")
        self.assertIs(module, teams_permissions)
        self.assertGreaterEqual(len(module.__dict__), 2)
