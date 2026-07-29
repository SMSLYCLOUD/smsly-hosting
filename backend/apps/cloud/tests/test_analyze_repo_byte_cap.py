# pylint: disable=invalid-name
"""
Regression tests for Issue 65 (analyze_repo clones a public repo
with no total byte cap).

Before the fix, a single 100MB Python file in a public repo
would be fully read into memory by the analyzer. After the fix,
``analyze_repo`` aborts with HTTP 413 if the total walk exceeds
the per-repo byte cap (50MB).
"""

import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


def _populate_repo(root, total_bytes):
    """Write a single file of approximately ``total_bytes``."""
    with open(os.path.join(root, 'big.py'), 'wb') as fh:
        fh.write(b'x' * total_bytes)


class AnalyzeRepoByteCapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='repo-byte-user', password='123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = '/api/v1/cloud/intelligence/analyze_repo/'

    def _call_analyze_with_repo(self, total_bytes):
        with tempfile.TemporaryDirectory() as tmp:
            _populate_repo(tmp, total_bytes)
            with patch(
                'apps.cloud.services.git_manager.GitManager.clone_repo',
                return_value=tmp,
            ), patch(
                'apps.deployments.utils.get_github_oauth_token_for_user',
                return_value=None,
            ):
                return self.client.post(
                    self.url,
                    {'repo_url': 'https://github.com/example/big'},
                    format='json',
                )

    def test_repo_within_cap_does_not_return_413(self):
        # 10MB is well within the 50MB cap.
        resp = self._call_analyze_with_repo(10 * 1024 * 1024)
        self.assertNotEqual(resp.status_code, 413)

    def test_repo_over_cap_returns_413(self):
        # 60MB exceeds the 50MB cap.
        resp = self._call_analyze_with_repo(60 * 1024 * 1024)
        self.assertEqual(resp.status_code, 413)
        self.assertIn('too large', str(resp.data).lower())
