"""Tests for GitHub client utility."""
from unittest.mock import patch, MagicMock

from django.test import TestCase

from apps.scripts.github import _extract_owner_repo, GitHubClient


class ExtractOwnerRepoTests(TestCase):
    def test_https_url(self):
        owner, repo = _extract_owner_repo('https://github.com/owner/repo')
        self.assertEqual(owner, 'owner')
        self.assertEqual(repo, 'repo')

    def test_https_url_with_git_suffix(self):
        owner, repo = _extract_owner_repo('https://github.com/owner/repo.git')
        self.assertEqual(owner, 'owner')
        self.assertEqual(repo, 'repo')

    def test_ssh_url(self):
        owner, repo = _extract_owner_repo('git@github.com:owner/repo')
        self.assertEqual(owner, 'owner')
        self.assertEqual(repo, 'repo')

    def test_ssh_url_with_git_suffix(self):
        owner, repo = _extract_owner_repo('git@github.com:owner/repo.git')
        self.assertEqual(owner, 'owner')
        self.assertEqual(repo, 'repo')

    def test_trailing_slash_stripped(self):
        owner, repo = _extract_owner_repo('https://github.com/owner/repo/')
        self.assertEqual(owner, 'owner')
        self.assertEqual(repo, 'repo')

    def test_invalid_url_raises(self):
        with self.assertRaises(ValueError):
            _extract_owner_repo('not-a-url')

    def test_single_segment_raises(self):
        with self.assertRaises(ValueError):
            _extract_owner_repo('only-one')


class GitHubClientTests(TestCase):
    def test_init_with_token(self):
        client = GitHubClient('https://github.com/owner/repo', token='tok123')
        self.assertEqual(client.owner, 'owner')
        self.assertEqual(client.repo, 'repo')
        self.assertEqual(client.token, 'tok123')

    @patch('apps.scripts.github._get_system_token', return_value='sys-token')
    def test_init_falls_back_to_system_token(self, mock_sys):
        client = GitHubClient('https://github.com/owner/repo')
        self.assertEqual(client.token, 'sys-token')

    @patch('apps.scripts.github._get_system_token', return_value=None)
    def test_init_no_token(self, mock_sys):
        client = GitHubClient('https://github.com/owner/repo')
        self.assertIsNone(client.token)
