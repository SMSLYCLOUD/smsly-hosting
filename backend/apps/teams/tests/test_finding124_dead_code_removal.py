"""
Regression tests for Finding #124 (dead code).

``IsTeamAdminOrMember`` was an unused permission class in
``apps/teams/permissions.py`` and was removed; the follow-up cleanup
also removed ``IsTeamMember``/``IsTeamAdmin`` (function-based checks
``user_can_read``/``user_can_write``/``assert_can_*`` are the API now).
These tests pin the cleaned state: no dead classes remain and the
module still imports cleanly.
"""
import importlib

from django.test import SimpleTestCase

from apps.teams import permissions as teams_permissions


class Finding124DeadCodeRemovalTests(SimpleTestCase):
    def test_is_team_admin_or_member_class_is_removed(self):
        self.assertFalse(hasattr(teams_permissions, "IsTeamAdminOrMember"))

    def test_remaining_permission_classes_still_present(self):
        self.assertFalse(hasattr(teams_permissions, "IsTeamMember"))
        self.assertFalse(hasattr(teams_permissions, "IsTeamAdmin"))
        for helper in (
            "user_can_read",
            "user_can_write",
            "assert_can_write",
            "assert_can_delete",
            "get_team_q_filter",
        ):
            self.assertTrue(
                callable(getattr(teams_permissions, helper, None)), helper,
            )

    def test_permissions_module_imports_cleanly(self):
        module = importlib.import_module("apps.teams.permissions")
        self.assertIs(module, teams_permissions)
        self.assertGreaterEqual(len(module.__dict__), 2)
