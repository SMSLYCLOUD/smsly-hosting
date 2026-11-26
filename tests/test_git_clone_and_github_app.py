"""Unit tests for git protocol.allow config, GitHub App token payload, and .git stripping."""

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = str(REPO_ROOT / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _load_github_app_mod():
    try:
        from apps.deployments.services import github_app
        return github_app
    except ModuleNotFoundError:
        fake_jwt = ModuleType("jwt")
        fake_jwt.encode = lambda *a, **kw: "fake.jwt"
        fake_requests = ModuleType("requests")
        fake_requests.Timeout = TimeoutError
        fake_requests.post = MagicMock()
        fake_requests.get = MagicMock()
        with patch.dict(sys.modules, {"jwt": fake_jwt, "requests": fake_requests}):
            mod_path = REPO_ROOT / "backend" / "apps" / "deployments" / "services" / "github_app.py"
            spec = importlib.util.spec_from_file_location("github_app_test_mod", mod_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod


def _extract_repo_full_name(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in (parsed.path or "").strip("/").split("/") if p][:2]
    if len(parts) == 2 and parts[1].lower().endswith(".git"):
        parts[1] = parts[1][:-4]
    return "/".join(parts)


class TestGitCloneAndGitHubAppFixes(unittest.TestCase):
    def test_no_invalid_protocol_allow_comma_syntax(self):
        for rel in (
            "backend/apps/deployments/services/pipeline/clone.py",
            "backend/apps/cloud/services/git_manager.py",
        ):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("protocol.allow=https,ssh", src, f"{rel} still has invalid protocol.allow=https,ssh")
            self.assertIn("protocol.allow=never", src)
            self.assertIn("protocol.https.allow=always", src)
            self.assertIn("protocol.ssh.allow=always", src)
            self.assertIn("GIT_ALLOW_PROTOCOL", src)

    def test_real_git_accepts_per_protocol_allow_config(self):
        env = {**os.environ, "GIT_ALLOW_PROTOCOL": "https:ssh"}
        proc = subprocess.run(
            [
                "git",
                "-c", "http.followRedirects=false",
                "-c", "protocol.file.allow=never",
                "-c", "protocol.allow=never",
                "-c", "protocol.https.allow=always",
                "-c", "protocol.ssh.allow=always",
                "config", "--get", "protocol.https.allow",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "always")

    def test_github_app_create_token_sends_string_names_and_repository_ids_fallback(self):
        gh_mod = _load_github_app_mod()
        svc = gh_mod.GitHubAppService(app_id="12345", private_key_pem="fake-key")

        # 1. Direct string repository name request succeeds (201)
        resp_201 = MagicMock(status_code=201)
        resp_201.json.return_value = {"token": "ghs_test_token"}
        with patch.object(gh_mod.requests, "post", return_value=resp_201) as mock_post:
            tok = svc._create_installation_token(163012430, "smsly-rate-limit-service", {"Authorization": "Bearer jwt"})

        self.assertEqual(tok, "ghs_test_token")
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.kwargs["json"], {"repositories": ["smsly-rate-limit-service"]})

        # 2. 422 fallback resolves integer ID and sends repository_ids (NOT repositories)
        resp_422 = MagicMock(status_code=422, text="unprocessable")
        resp_fallback_201 = MagicMock(status_code=201)
        resp_fallback_201.json.return_value = {"token": "ghs_id_token"}
        with patch.object(svc, "_resolve_repo_id", return_value=987654321), patch.object(
            gh_mod.requests,
            "post",
            side_effect=[resp_422, resp_fallback_201],
        ) as mock_post2:
            tok2 = svc._create_installation_token(163012430, "smsly-rate-limit-service", {"Authorization": "Bearer jwt"})

        self.assertEqual(tok2, "ghs_id_token")
        self.assertEqual(mock_post2.call_args_list[0].kwargs["json"], {"repositories": ["smsly-rate-limit-service"]})
        self.assertEqual(mock_post2.call_args_list[1].kwargs["json"], {"repository_ids": [987654321]})

    def test_git_suffix_preservation(self):
        for rel in (
            "backend/apps/deployments/services/pipeline/clone.py",
            "backend/apps/deployments/services/remote_orchestrator/deployment.py",
            "backend/apps/scripts/github.py",
        ):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn('.rstrip(".git")', src, f"{rel} still uses .rstrip('.git')")
            self.assertNotIn(".rstrip('.git')", src, f"{rel} still uses .rstrip('.git')")

        self.assertEqual(
            _extract_repo_full_name("https://github.com/SMSLYCLOUD/smsly-ai-smart"),
            "SMSLYCLOUD/smsly-ai-smart",
        )
        self.assertEqual(
            _extract_repo_full_name("https://github.com/SMSLYCLOUD/smsly-hosting.git"),
            "SMSLYCLOUD/smsly-hosting",
        )
        self.assertEqual(
            _extract_repo_full_name("https://github.com/SMSLYCLOUD/smsly-rate-limit-service"),
            "SMSLYCLOUD/smsly-rate-limit-service",
        )


if __name__ == "__main__":
    unittest.main()
