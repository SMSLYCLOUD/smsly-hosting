import logging
from typing import Any, Dict, Optional

import requests

from django.conf import settings

from apps.deployments.utils import get_github_oauth_token_for_user

logger = logging.getLogger(__name__)


_GITHUB_API_BASE = "https://api.github.com"


def _extract_owner_repo(repo_url: str) -> tuple[str, str]:
    repo_url = repo_url.rstrip(".git").rstrip("/")
    # Handle SSH URLs like git@github.com:owner/repo
    if ":" in repo_url and "/" not in repo_url.split(":")[0]:
        after_colon = repo_url.split(":", 1)[1]
        parts = after_colon.split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
    # Handle HTTPS URLs like https://github.com/owner/repo
    if "/" in repo_url:
        parts = repo_url.split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
    raise ValueError(f"Cannot parse owner/repo from: {repo_url}")


def _get_system_token() -> Optional[str]:
    return getattr(settings, "GITHUB_SYSTEM_TOKEN", None) or None


class GitHubClient:
    def __init__(
        self,
        repo_url: str,
        token: Optional[str] = None,
        owner: Optional[Any] = None,
    ):
        self.owner, self.repo = _extract_owner_repo(repo_url)
        self.token = token or _get_system_token()
        self._owner_obj = owner

    def _resolve_token(self) -> Optional[str]:
        if self.token:
            return self.token
        if self._owner_obj:
            return get_github_oauth_token_for_user(self._owner_obj)
        return None

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = self._resolve_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
    ) -> Dict[str, Any]:
        url = f"{_GITHUB_API_BASE}/repos/{repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        if resp.status_code in (201, 200):
            return resp.json()
        logger.error(
            "GitHub PR creation failed (%s): %s",
            resp.status_code,
            resp.text,
        )
        resp.raise_for_status()
        return {}

    def get_repository(self, repo: str) -> Dict[str, Any]:
        url = f"{_GITHUB_API_BASE}/repos/{repo}"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code == 200:
            return resp.json()
        logger.error(
            "GitHub get_repo failed (%s): %s",
            resp.status_code,
            resp.text,
        )
        resp.raise_for_status()
        return {}
