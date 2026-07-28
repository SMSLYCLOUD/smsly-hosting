# pylint: disable=invalid-name
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class Finding129AnalyzeRepoErrorRedactionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="analyze-err-129", password="x",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = "/api/v1/cloud/intelligence/analyze_repo/"

    def test_github_404_error_returns_generic_message_without_stderr(self):
        raw_git_stderr = (
            "remote: Repository not found.\n"
            "fatal: repository 'https://github.com/secret-org/private.git/' "
            "not found\n"
        )
        with patch(
            "apps.cloud.services.git_manager.GitManager.clone_repo",
            side_effect=Exception(raw_git_stderr),
        ), patch(
            "apps.deployments.utils.get_github_oauth_token_for_user",
            return_value=None,
        ):
            resp = self.client.post(
                self.url,
                {"repo_url": "https://github.com/secret-org/private"},
                format="json",
            )

        self.assertEqual(resp.status_code, 400)
        body = str(resp.data)
        self.assertIn("Repository not found or inaccessible", body)
        self.assertNotIn("fatal:", body)
        self.assertNotIn("secret-org", body)
        self.assertNotIn("not found\n", body)
        self.assertNotIn(raw_git_stderr, body)

    def test_github_auth_error_returns_generic_message_without_token_leak(self):
        raw_git_stderr = (
            "remote: Invalid username or password.\n"
            "fatal: Authentication failed for "
            "'https://user:supersecrettoken@github.com/x/y.git/'\n"
        )
        with patch(
            "apps.cloud.services.git_manager.GitManager.clone_repo",
            side_effect=Exception(raw_git_stderr),
        ), patch(
            "apps.deployments.utils.get_github_oauth_token_for_user",
            return_value=None,
        ):
            resp = self.client.post(
                self.url,
                {"repo_url": "https://github.com/x/y"},
                format="json",
            )

        self.assertEqual(resp.status_code, 400)
        body = str(resp.data)
        self.assertIn("Repository not found or inaccessible", body)
        self.assertNotIn("supersecrettoken", body)
        self.assertNotIn("Authentication failed", body)
        self.assertNotIn("fatal:", body)
