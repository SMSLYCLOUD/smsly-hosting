# pylint: disable=invalid-name
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.core import Service
from apps.deployments.services.safedeploy.branch_preview_manager import (
    BRANCH_NAME_RE,
    COMMIT_SHA_RE,
    BranchPreviewManager,
)

User = get_user_model()


class Finding108BranchPreviewValidateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="bpm-108", password="x",
        )
        self.service = Service.objects.create(
            name="bpm-svc-108", owner=self.user,
        )
        self.manager = BranchPreviewManager()

    def test_branch_name_regex_accepts_safe_inputs(self):
        safe_values = [
            "main",
            "feature/new-billing",
            "release_2.0",
            "u.fix.42",
            "a" * 200,
        ]
        for value in safe_values:
            self.assertRegex(
                value, BRANCH_NAME_RE,
                f"expected {value!r} to be accepted by BRANCH_NAME_RE",
            )

    def test_branch_name_regex_rejects_unsafe_inputs(self):
        unsafe_values = [
            "",
            "a" * 201,
            "branch with spaces",
            "branch;rm -rf /",
            "branch\ninjected",
            "branch$var",
            "branch`cmd`",
        ]
        for value in unsafe_values:
            self.assertNotRegex(
                value, BRANCH_NAME_RE,
                f"expected {value!r} to be rejected by BRANCH_NAME_RE",
            )

    def test_commit_sha_regex_accepts_safe_inputs(self):
        safe_values = [
            "abc1234",
            "0123456789abcdef",
            "a" * 40,
        ]
        for value in safe_values:
            self.assertRegex(
                value, COMMIT_SHA_RE,
                f"expected {value!r} to be accepted by COMMIT_SHA_RE",
            )

    def test_commit_sha_regex_rejects_unsafe_inputs(self):
        unsafe_values = [
            "",
            "abc",
            "abc1234z",
            "g" * 7,
            "a" * 41,
            "branch;rm",
            "../../etc/passwd",
        ]
        for value in unsafe_values:
            self.assertNotRegex(
                value, COMMIT_SHA_RE,
                f"expected {value!r} to be rejected by COMMIT_SHA_RE",
            )

    def test_create_preview_rejects_unsafe_branch_name(self):
        with self.assertRaises(ValueError):
            self.manager.create_preview(
                service=self.service,
                branch_name="bad branch;rm -rf",
                commit_sha="abc1234",
                user=self.user,
            )

    def test_create_preview_rejects_unsafe_commit_sha(self):
        with self.assertRaises(ValueError):
            self.manager.create_preview(
                service=self.service,
                branch_name="main",
                commit_sha="not-a-sha",
                user=self.user,
            )

    def test_create_preview_accepts_valid_pair(self):
        preview = self.manager.create_preview(
            service=self.service,
            branch_name="main",
            commit_sha="abc1234",
            user=self.user,
        )
        self.assertEqual(preview.branch_name, "main")
        self.assertEqual(preview.commit_sha, "abc1234")
