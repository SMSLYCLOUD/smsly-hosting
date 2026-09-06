"""Tests for GitHub App media-repo staging (no credentials reach the node)."""
import io
import tarfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer
from apps.deployments.services.provisioner.helpers.media_repo import (
    _parse_repo_full_name,
    resolve_media_repo_url,
    stage_media_repo_for_node,
)

APP_TOKEN = "ghs_testtoken1234567890"


def _make_exec_mock(exit_code=0, stdout_text=b"", stderr_text=b""):
    out = MagicMock()
    out.channel.recv_exit_status.return_value = exit_code
    out.read.return_value = stdout_text
    err = MagicMock()
    err.read.return_value = stderr_text
    return (MagicMock(), out, err)


def _make_tarball_bytes():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (
            ("smsly-media-mgmt-abc123/Cargo.toml", b'[package]\nname = "smsly-media-mgmt"\n'),
            ("smsly-media-mgmt-abc123/src/main.rs", b"fn main() {}\n"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class ParseRepoFullNameTests(TestCase):
    def test_https(self):
        self.assertEqual(
            _parse_repo_full_name("https://github.com/SMSLYCLOUD/smsly-media-mgmt"),
            "SMSLYCLOUD/smsly-media-mgmt",
        )

    def test_https_dot_git(self):
        self.assertEqual(
            _parse_repo_full_name("https://github.com/o/r.git"),
            "o/r",
        )

    def test_scp_style(self):
        self.assertEqual(
            _parse_repo_full_name("git@github.com:SMSLYCLOUD/smsly-media-mgmt.git"),
            "SMSLYCLOUD/smsly-media-mgmt",
        )

    def test_non_github_rejected(self):
        self.assertEqual(_parse_repo_full_name("https://gitlab.com/o/r"), "")
        self.assertEqual(_parse_repo_full_name(""), "")
        self.assertEqual(_parse_repo_full_name("not a url"), "")
        self.assertEqual(_parse_repo_full_name("https://github.com/onlyowner"), "")


class ResolveMediaRepoUrlTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="media_repo_user", password="123")

    def tearDown(self):
        self.user.delete()

    def _server(self):
        return ManagedServer.objects.create(
            owner=self.user, name="m", host="139.84.248.162",
        )

    def test_default_without_profile_or_config(self):
        server = self._server()
        url = resolve_media_repo_url(server)
        self.assertIn("smsly-media-mgmt", url)
        server.delete()

    def test_profile_url_wins(self):
        from apps.media.models.node import MediaNodeProfile
        server = self._server()
        MediaNodeProfile.objects.create(
            server=server, script_repo_url="https://github.com/Acme/custom-mgmt",
        )
        try:
            self.assertEqual(
                resolve_media_repo_url(server),
                "https://github.com/Acme/custom-mgmt",
            )
        finally:
            server.delete()


class StageMediaRepoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="media_stage_user", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user, name="media-voice-video-1", host="139.84.248.162",
        )

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    def _mock_ssh(self):
        ssh = MagicMock()
        ssh.exec_command.return_value = _make_exec_mock()
        return ssh

    def _mock_requests(self):
        tarball = _make_tarball_bytes()
        lookup = MagicMock()
        lookup.ok = True
        lookup.json.return_value = {"default_branch": "main"}
        stream = MagicMock()
        stream.ok = True
        stream.__enter__.return_value = stream
        stream.__exit__.return_value = False
        stream.iter_content.return_value = [tarball[i:i + 65536] for i in range(0, len(tarball), 65536)]
        return lookup, stream

    def test_app_path_returns_file_url_and_empty_token(self):
        lookup, stream = self._mock_requests()
        ssh = self._mock_ssh()
        with patch(
            "apps.deployments.services.provisioner.helpers.media_repo._mint_app_token",
            return_value=APP_TOKEN,
        ), patch(
            "apps.deployments.services.provisioner.helpers.media_repo.requests"
        ) as mock_requests:
            mock_requests.get.side_effect = [lookup, stream]
            url, token = stage_media_repo_for_node(ssh, self.server)

        self.assertEqual(url, "file:///opt/smsly-media-src")
        self.assertEqual(token, "")
        # Two files uploaded via SFTP.
        sftp = ssh.open_sftp.return_value
        self.assertEqual(sftp.put.call_count, 2)
        # A local git repo was seeded on the node.
        seed_cmds = [
            call.args[0] for call in ssh.exec_command.call_args_list
        ]
        self.assertTrue(any("git init" in c for c in seed_cmds))
        self.assertTrue(any("git remote" in c for c in seed_cmds))

    def test_app_token_never_reaches_node_commands(self):
        lookup, stream = self._mock_requests()
        ssh = self._mock_ssh()
        with patch(
            "apps.deployments.services.provisioner.helpers.media_repo._mint_app_token",
            return_value=APP_TOKEN,
        ), patch(
            "apps.deployments.services.provisioner.helpers.media_repo.requests"
        ) as mock_requests:
            mock_requests.get.side_effect = [lookup, stream]
            stage_media_repo_for_node(ssh, self.server)

        for call in ssh.exec_command.call_args_list:
            for arg in call.args:
                self.assertNotIn(APP_TOKEN, str(arg))
        sftp = ssh.open_sftp.return_value
        for call in sftp.put.call_args_list:
            for arg in call.args:
                self.assertNotIn(APP_TOKEN, str(arg))

    def test_legacy_fallback_with_profile_token(self):
        from apps.media.models.node import MediaNodeProfile
        MediaNodeProfile.objects.create(
            server=self.server,
            script_repo_url="https://github.com/Acme/custom-mgmt",
            script_repo_token="legacy-profile-token",
        )
        ssh = self._mock_ssh()
        with patch(
            "apps.deployments.services.provisioner.helpers.media_repo._mint_app_token",
            return_value="",
        ):
            url, token = stage_media_repo_for_node(ssh, self.server)
        self.assertEqual(url, "https://github.com/Acme/custom-mgmt")
        self.assertEqual(token, "legacy-profile-token")
        # No staging I/O on the node in legacy mode.
        ssh.exec_command.assert_not_called()
