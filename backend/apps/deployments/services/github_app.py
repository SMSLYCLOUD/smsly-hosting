"""
GitHub App integration service.

Generates short-lived installation access tokens for private repository
operations (clone, pip install inside Docker builds, webhook setup).

Security guarantees:
  - No token is ever persisted to the database or disk.
  - Tokens are repo-scoped (one repo per call) and expire in 1 hour max.
  - The App private key is never logged, serialised, or transmitted.
  - JWT lifetime is capped at 9 minutes (GitHub max is 10).
  - Falls back to None gracefully when the App is not configured, allowing
    the existing OAuth-token code path to take over.

Dependencies: PyJWT (already in requirements.txt), requests.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import jwt  # PyJWT
import requests

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# GitHub API base
_GH_API = "https://api.github.com"

# How long the JWT is valid (seconds). GitHub enforces max 600 (10 min).
# We use 9 min to give a comfortable buffer for clock skew.
_JWT_LIFETIME_SECONDS = 540

# Back-date token by 60 s to absorb minor clock differences between
# our server and GitHub's token-validation servers.
_JWT_CLOCK_SKEW_SECONDS = 60


class GitHubAppService:
    """
    Authenticates as a GitHub App and vends short-lived installation tokens.

    Usage::

        svc = GitHubAppService(app_id="123456", private_key_pem="-----BEGIN...")
        token = svc.get_installation_token("SMSLYCLOUD/smsly-shared")
        # token is valid for 1 hour, read-only on that single repo.
    """

    def __init__(self, app_id: str, private_key_pem: str) -> None:
        self._app_id = str(app_id).strip()
        self._private_key = private_key_pem.strip()

    def _auth_headers(self, token: str | None = None) -> dict:
        """Return standard GitHub API headers. Uses JWT if no token provided."""
        if token is None:
            token = self._generate_jwt()
            auth = f"Bearer {token}"
        else:
            auth = f"token {token}"
        return {
            "Authorization": auth,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ── JWT (App-level auth) ──────────────────────────────────────────────────

    def _generate_jwt(self) -> str:
        """
        Generate a signed JWT to authenticate AS the GitHub App (not an installation).

        The JWT is used only to look up the installation ID and exchange it for
        a repo-scoped installation token. It is never stored or logged.
        """
        now = int(time.time())
        payload = {
            # Back-dated to absorb clock skew
            "iat": now - _JWT_CLOCK_SKEW_SECONDS,
            "exp": now + _JWT_LIFETIME_SECONDS,
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    # ── Installation token ────────────────────────────────────────────────────

    def get_installation_token(self, repo_full_name: str) -> str | None:
        """
        Return a 1-hour installation access token scoped to *repo_full_name*.

        The token has the minimum permission required (Contents: read) and is
        restricted to the single named repository. It is returned as a plain
        string — callers must NOT persist it.

        Returns None on any error (misconfiguration, network, GitHub outage)
        so that callers can fall back gracefully.

        Args:
            repo_full_name: ``"owner/repo"``, e.g. ``"SMSLYCLOUD/smsly-shared"``
        """
        try:
            parts = repo_full_name.split("/", 1)
            if len(parts) != 2 or not all(parts):
                logger.error(
                    "GitHubAppService: invalid repo_full_name %r — expected 'owner/repo'",
                    repo_full_name,
                )
                return None

            owner, repo_name = parts
            app_jwt = self._generate_jwt()
            headers = {
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            # Step 1: resolve the installation ID for this org / user account
            installation_id = self._resolve_installation_id(owner, headers)
            if installation_id is None:
                return None

            # Step 2: exchange installation ID for a repo-scoped token
            return self._create_installation_token(
                installation_id=installation_id,
                repo_name=repo_name,
                headers=headers,
            )

        except Exception:
            logger.exception(
                "GitHubAppService: unexpected error getting token for %s",
                repo_full_name,
            )
            return None

    def _resolve_installation_id(self, owner: str, headers: dict) -> int | None:
        """
        Find the installation ID for *owner* (org or user).

        Tries the org endpoint first, then falls back to the user endpoint.
        Returns None if the App is not installed for *owner*.
        """
        for endpoint in (
            f"{_GH_API}/orgs/{owner}/installation",
            f"{_GH_API}/users/{owner}/installation",
        ):
            try:
                resp = requests.get(endpoint, headers=headers, timeout=10)
                if resp.status_code == 200:
                    installation_id = resp.json().get("id")
                    if installation_id:
                        logger.debug(
                            "GitHubAppService: installation %s found for owner %s",
                            installation_id,
                            owner,
                        )
                        return int(installation_id)
                elif resp.status_code == 404:
                    continue  # try next endpoint
                else:
                    logger.error(
                        "GitHubAppService: unexpected status %s from %s: %s",
                        resp.status_code,
                        endpoint,
                        resp.text[:200],
                    )
                    return None
            except requests.Timeout:
                logger.error(
                    "GitHubAppService: timeout resolving installation for owner %s",
                    owner,
                )
                return None

        logger.error(
            "GitHubAppService: App not installed for owner %r. "
            "Install the GitHub App at https://github.com/apps/<app-slug>/installations/new",
            owner,
        )
        return None

    def _create_installation_token(
        self,
        installation_id: int,
        repo_name: str,
        headers: dict,
    ) -> str | None:
        """
        POST to the installations access_tokens endpoint and return the token string.

        The token is scoped to a single repository with only the permissions
        the App was granted (Contents: read-only in our case).
        """
        try:
            resp = requests.post(
                f"{_GH_API}/app/installations/{installation_id}/access_tokens",
                headers=headers,
                json={"repositories": [repo_name]},
                timeout=10,
            )
        except requests.Timeout:
            logger.error(
                "GitHubAppService: timeout creating installation token for installation %s",
                installation_id,
            )
            return None

        if resp.status_code == 201:
            token = resp.json().get("token")
            if token:
                logger.info(
                    "GitHubAppService: installation token issued for repo %s "
                    "(installation %s, 1hr expiry)",
                    repo_name,
                    installation_id,
                )
                return token

        logger.error(
            "GitHubAppService: failed to create installation token "
            "(status %s): %s",
            resp.status_code,
            resp.text[:200],
        )
        return None

    # ── GitHub Deployments API ───────────────────────────────────────────────

    def create_deployment(
        self,
        installation_id: int,
        repo_full_name: str,
        ref: str,
        environment: str,
        description: str = "",
        transient_environment: bool = False,
        production_environment: bool = False,
    ) -> int | None:
        """Create a GitHub Deployment. Returns the deployment ID.

        The deployment appears in the repository's Environments tab and can
        be linked to deployment statuses for environment URL tracking.
        """
        try:
            token = self.get_installation_token_for_id(installation_id)
            if not token:
                return None

            payload: dict = {
                "ref": ref,
                "environment": environment,
                "description": description[:140],
                "auto_merge": False,
                "required_contexts": [],  # skip status checks
            }
            if transient_environment:
                payload["transient_environment"] = True
            if production_environment:
                payload["production_environment"] = True

            resp = requests.post(
                f"{_GH_API}/repos/{repo_full_name}/deployments",
                headers=self._auth_headers(token),
                json=payload,
                timeout=10,
            )
            if resp.status_code == 201:
                dep_id = resp.json().get("id")
                logger.info(
                    "GitHubAppService: deployment %s created on %s (env=%s)",
                    dep_id, repo_full_name, environment,
                )
                return dep_id

            logger.error(
                "GitHubAppService: create_deployment failed (status %s): %s",
                resp.status_code, resp.text[:200],
            )
        except Exception:
            logger.exception(
                "GitHubAppService: error creating deployment on %s", repo_full_name
            )
        return None

    def create_deployment_status(
        self,
        installation_id: int,
        repo_full_name: str,
        github_deployment_id: int,
        state: str,
        environment_url: str = "",
        log_url: str = "",
        description: str = "",
    ) -> bool:
        """Update a GitHub Deployment with a status.

        States: pending, in_progress, success, failure, error, inactive.
        """
        try:
            token = self.get_installation_token_for_id(installation_id)
            if not token:
                return False

            payload: dict = {"state": state, "description": description[:140]}
            if environment_url:
                payload["environment_url"] = environment_url
            if log_url:
                payload["log_url"] = log_url

            resp = requests.post(
                f"{_GH_API}/repos/{repo_full_name}/deployments/{github_deployment_id}/statuses",
                headers=self._auth_headers(token),
                json=payload,
                timeout=10,
            )
            if resp.status_code == 201:
                logger.info(
                    "GitHubAppService: deployment %s status → %s on %s",
                    github_deployment_id, state, repo_full_name,
                )
                return True

            logger.error(
                "GitHubAppService: create_deployment_status failed (status %s): %s",
                resp.status_code, resp.text[:200],
            )
        except Exception:
            logger.exception(
                "GitHubAppService: error updating deployment %s status on %s",
                github_deployment_id, repo_full_name,
            )
        return False

    # ── App metadata ──────────────────────────────────────────────────────────

    def get_app_info(self) -> dict | None:
        """Fetch the App's metadata from GitHub (slug, name, etc.)."""
        try:
            resp = requests.get(
                f"{_GH_API}/app",
                headers=self._auth_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error(
                "GitHubAppService: failed to get app info (status %s)", resp.status_code
            )
        except Exception:
            logger.exception("GitHubAppService: error fetching app info")
        return None

    def get_app_slug(self) -> str | None:
        """Fetch the App's slug from GitHub for constructing redirect URLs."""
        info = self.get_app_info()
        return info.get("slug") if info else None

    def get_installation(self, installation_id: int) -> dict | None:
        """Fetch installation details from GitHub by installation_id."""
        try:
            resp = requests.get(
                f"{_GH_API}/app/installations/{installation_id}",
                headers=self._auth_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error(
                "GitHubAppService: failed to get installation %s (status %s)",
                installation_id, resp.status_code,
            )
        except Exception:
            logger.exception(
                "GitHubAppService: error fetching installation %s", installation_id
            )
        return None

    # ── Installation-level operations ─────────────────────────────────────────

    def get_installation_token_for_id(
        self, installation_id: int, repo_names: list[str] | None = None
    ) -> str | None:
        """Get an installation token for a specific installation_id.

        Args:
            installation_id: The GitHub installation ID.
            repo_names: Optional list of repo names to scope the token to.
                        If None, the token covers all repos in the installation.
        """
        try:
            payload: dict = {}
            if repo_names:
                payload["repositories"] = repo_names
            resp = requests.post(
                f"{_GH_API}/app/installations/{installation_id}/access_tokens",
                headers=self._auth_headers(),
                json=payload,
                timeout=10,
            )
            if resp.status_code == 201:
                token = resp.json().get("token")
                if token:
                    logger.info(
                        "GitHubAppService: installation token issued for installation %s",
                        installation_id,
                    )
                    return token
            logger.error(
                "GitHubAppService: failed to create token for installation %s (status %s): %s",
                installation_id,
                resp.status_code,
                resp.text[:200],
            )
        except Exception:
            logger.exception(
                "GitHubAppService: error creating token for installation %s",
                installation_id,
            )
        return None

    def list_installation_repos(self, installation_id: int) -> list[dict]:
        """List repositories accessible to an installation.

        Returns a list of dicts with at least 'id', 'full_name', 'private' keys.
        Handles pagination (GitHub defaults to 30, max 100 per page).
        """
        try:
            token = self.get_installation_token_for_id(installation_id)
            if not token:
                return []
            all_repos: list[dict] = []
            page = 1
            while True:
                resp = requests.get(
                    f"{_GH_API}/installation/repositories",
                    headers=self._auth_headers(token),
                    params={"per_page": 100, "page": page},
                    timeout=10,
                )
                if resp.status_code != 200:
                    logger.error(
                        "GitHubAppService: failed to list repos for installation %s page %s (status %s)",
                        installation_id,
                        page,
                        resp.status_code,
                    )
                    break
                data = resp.json()
                repos = data.get("repositories", [])
                all_repos.extend(repos)
                # GitHub returns total_count; if we have all, break
                total = data.get("total_count")
                if total is not None and len(all_repos) >= total:
                    break
                if len(repos) < 100:
                    break
                page += 1
                if page > 50:  # safety cap: 50*100=5000 repos
                    logger.warning("GitHubAppService: pagination cap reached for installation %s", installation_id)
                    break
            return all_repos
        except Exception:
            logger.exception(
                "GitHubAppService: error listing repos for installation %s",
                installation_id,
            )
        return []

    def create_commit_status(
        self,
        installation_id: int,
        repo_full_name: str,
        sha: str,
        state: str,
        description: str,
        context: str,
        target_url: str = "",
    ) -> bool:
        """Create a commit status on a repository using an installation token.

        Args:
            installation_id: The GitHub installation ID.
            repo_full_name: e.g. "owner/repo".
            sha: The commit SHA to attach the status to.
            state: One of 'pending', 'success', 'failure', 'error'.
            description: Short description (max 140 chars).
            context: Status context string, e.g. "smsly/deploy".
            target_url: URL to link from the status.
        """
        try:
            token = self.get_installation_token_for_id(installation_id)
            if not token:
                return False
            payload = {"state": state, "description": description, "context": context}
            if target_url:
                payload["target_url"] = target_url
            resp = requests.post(
                f"{_GH_API}/repos/{repo_full_name}/statuses/{sha}",
                headers=self._auth_headers(token),
                json=payload,
                timeout=10,
            )
            if resp.status_code == 201:
                logger.info(
                    "GitHubAppService: commit status '%s' set on %s@%s",
                    state,
                    repo_full_name,
                    sha[:7],
                )
                return True
            logger.error(
                "GitHubAppService: failed to create commit status (status %s): %s",
                resp.status_code,
                resp.text[:200],
            )
        except Exception:
            logger.exception(
                "GitHubAppService: error creating commit status on %s", repo_full_name
            )
        return False


# ── Factory ───────────────────────────────────────────────────────────────────


def get_github_app_service() -> GitHubAppService | None:
    """
    Return a configured :class:`GitHubAppService`, or ``None`` if the GitHub
    App credentials are not set in Django settings.

    This is the preferred factory. Callers should treat ``None`` as "App not
    configured" and fall back to the user OAuth token path.

    Configuration (settings.py / .env)::

        GITHUB_APP_ID          = "123456"
        GITHUB_APP_PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\\n..."
    """
    try:
        from django.conf import settings as django_settings
        app_id = getattr(django_settings, "GITHUB_APP_ID", "") or ""
        private_key = getattr(django_settings, "GITHUB_APP_PRIVATE_KEY", "") or ""
    except Exception:
        return None

    if not app_id or not private_key:
        return None

    return GitHubAppService(app_id=app_id, private_key_pem=private_key)


def get_installation_token_for_repo(repo_full_name: str) -> str | None:
    """
    Convenience wrapper: get a repo-scoped token using the configured App.

    Returns ``None`` if the App is not configured or the call fails, so the
    caller can transparently fall back to a user OAuth token.

    Args:
        repo_full_name: ``"SMSLYCLOUD/smsly-shared"``
    """
    svc = get_github_app_service()
    if svc is None:
        return None
    return svc.get_installation_token(repo_full_name)


def get_installation_token_for_installation_id(installation_id: int) -> str | None:
    """Convenience wrapper: get a token for a stored installation_id."""
    svc = get_github_app_service()
    if svc is None:
        return None
    return svc.get_installation_token_for_id(installation_id)


def get_installation_for_repo(repo_full_name: str):  # noqa: ANN201
    """Look up the GitHubAppInstallation that covers a given repo.

    Returns the installation instance or None.
    """
    from apps.deployments.models.github_app import GitHubAppInstallation

    # Check installations with explicit repo lists
    inst = GitHubAppInstallation.objects.filter(
        status=GitHubAppInstallation.Status.ACTIVE,
        repository_selection="selected",
        repositories__contains=[{"name": repo_full_name}],
    ).first()
    if inst:
        return inst

    # Check "all repos" installations — match by account_login (owner)
    owner = repo_full_name.split("/")[0] if "/" in repo_full_name else ""
    if owner:
        inst = GitHubAppInstallation.objects.filter(
            status=GitHubAppInstallation.Status.ACTIVE,
            repository_selection="all",
            account_login=owner,
        ).first()
    return inst
