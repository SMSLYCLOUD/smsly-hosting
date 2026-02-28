"""Tests for repository cache branch fallback behavior."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from services.repo_cache import _safe_stderr, get_or_clone


class RepoCacheTests(SimpleTestCase):
    def test_safe_stderr_decodes_bytes(self):
        exc = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "clone"],
            stderr=b"fatal: branch not found",
        )
        self.assertEqual(_safe_stderr(exc), "fatal: branch not found")

    @patch("services.repo_cache._cache_path")
    @patch("services.repo_cache._detect_bare_default_branch", return_value="master")
    @patch("services.repo_cache._clone_worktree")
    @patch("services.repo_cache._fetch_bare")
    def test_get_or_clone_retries_with_default_branch_when_requested_branch_missing(
        self,
        fetch_mock,
        clone_worktree_mock,
        detect_branch_mock,
        cache_path_mock,
    ):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "repo-cache"
            bare_dir = cache_dir / "bare.git"
            bare_dir.mkdir(parents=True, exist_ok=True)
            (bare_dir / "HEAD").write_text(
                "ref: refs/heads/main\n",
                encoding="utf-8",
            )
            cache_path_mock.return_value = cache_dir

            clone_worktree_mock.side_effect = [
                subprocess.CalledProcessError(
                    returncode=128,
                    cmd=["git", "clone"],
                    stderr="fatal: Remote branch main not found in upstream origin",
                ),
                None,
            ]

            result = get_or_clone("https://github.com/acme/service", branch="main", token=None)

            self.assertIn("worktree-master-", Path(result).name)
            self.assertEqual(fetch_mock.call_count, 1)
            self.assertEqual(detect_branch_mock.call_count, 1)
            self.assertEqual(clone_worktree_mock.call_count, 2)
            self.assertEqual(clone_worktree_mock.call_args_list[0][0][1], "main")
            self.assertEqual(clone_worktree_mock.call_args_list[1][0][1], "master")
