import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.deployments.services.pipeline import PipelineManager


class PipelineCloneTests(SimpleTestCase):
    def test_token_clone_keeps_askpass_outside_destination(self):
        manager = object.__new__(PipelineManager)
        manager.secret_values = []

        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp) / "repo"

            def fake_run(cmd, **kwargs):
                clone_dest = Path(cmd[-1])
                self.assertEqual(clone_dest.parent, Path(tmp))
                self.assertTrue(clone_dest.name.startswith(".clone_tmp_"))
                self.assertFalse(target_dir.exists())

                askpass_path = Path(kwargs["env"]["GIT_ASKPASS"])
                self.assertTrue(askpass_path.exists())
                self.assertEqual(askpass_path.parent, Path(tmp))
                self.assertNotEqual(askpass_path.parent, target_dir)
                self.assertEqual(kwargs["env"]["SMSLY_GIT_PASSWORD"], "secret-token")
                return subprocess.CompletedProcess(cmd, 0)

            with patch(
                "apps.deployments.services.pipeline.clone.subprocess.run",
                side_effect=fake_run,
            ) as mock_run:
                manager._clone_with_github_token(
                    "https://github.com/SMSLYCLOUD/smsly-frontend.git",
                    "main",
                    "secret-token",
                    str(target_dir),
                )

            self.assertEqual(mock_run.call_count, 1)
            self.assertFalse(list(Path(tmp).glob(".smsly-git-askpass-*.sh")))

    def test_clone_error_includes_redacted_git_stderr(self):
        manager = object.__new__(PipelineManager)
        manager.secret_values = []

        exc = subprocess.CalledProcessError(
            128,
            ["git", "clone"],
            stderr="fatal: Authentication failed for secret-token",
        )

        message = manager._format_git_clone_error(exc, "secret-token")

        self.assertIn("git clone exited with code 128", message)
        self.assertIn("fatal: Authentication failed for ***", message)
        self.assertNotIn("secret-token", message)

    def test_empty_resume_directory_is_not_available_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "repo"
            source_dir.mkdir()

            self.assertFalse(PipelineManager._source_tree_available(str(source_dir)))

            (source_dir / ".git").mkdir()
            self.assertTrue(PipelineManager._source_tree_available(str(source_dir)))

    def test_target_commit_checkout_places_dashdash_after_revision(self):
        manager = object.__new__(PipelineManager)
        manager.secret_values = []
        sha = "717e2197b28511d9c0bb4748a79f7cb0757ff173"

        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp) / "repo"
            seen_cmds = []

            def fake_run(cmd, **kwargs):
                seen_cmds.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0)

            with patch(
                "apps.deployments.services.pipeline.clone.subprocess.run",
                side_effect=fake_run,
            ):
                manager._clone_with_github_token(
                    "https://github.com/SMSLYCLOUD/SMSLY-Marketing-Campaigns",
                    "main",
                    None,
                    str(target_dir),
                    target_commit=sha,
                )

            self.assertEqual(len(seen_cmds), 2)
            self.assertEqual(
                seen_cmds[1],
                ["git", "checkout", "--detach", sha, "--"],
            )

    def test_target_commit_missing_falls_back_to_origin_fetch(self):
        manager = object.__new__(PipelineManager)
        manager.secret_values = []
        sha = "717e2197b28511d9c0bb4748a79f7cb0757ff173"

        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp) / "repo"
            seen_cmds = []

            def fake_run(cmd, **kwargs):
                seen_cmds.append(list(cmd))
                if cmd[:3] == ["git", "checkout", "--detach"] and sha in cmd:
                    raise subprocess.CalledProcessError(1, cmd, stderr="reference is not a tree")
                return subprocess.CompletedProcess(cmd, 0)

            with patch(
                "apps.deployments.services.pipeline.clone.subprocess.run",
                side_effect=fake_run,
            ):
                manager._clone_with_github_token(
                    "https://github.com/SMSLYCLOUD/SMSLY-Marketing-Campaigns",
                    "main",
                    None,
                    str(target_dir),
                    target_commit=sha,
                )

            self.assertEqual(len(seen_cmds), 4)
            self.assertIn("fetch", seen_cmds[2])
            self.assertIn("protocol.allow=never", seen_cmds[2])
            self.assertEqual(seen_cmds[2][-2:], ["origin", sha])
            self.assertEqual(seen_cmds[3], ["git", "checkout", "--detach", "FETCH_HEAD", "--"])
