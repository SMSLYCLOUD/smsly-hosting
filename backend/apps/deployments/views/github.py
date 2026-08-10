"""GitHub integration views — repository listing for deployment UI."""

from __future__ import annotations

import logging
from typing import Any

import requests
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _get_github_token(user):
    """Retrieve the stored GitHub OAuth token for *user*, refreshing if expired."""
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken

        account = (
            SocialAccount.objects.filter(user=user, provider="github")
            .order_by("-id")
            .first()
        )
        if not account:
            return None
        token_obj = (
            SocialToken.objects.filter(account=account)
            .order_by("-id")
            .first()
        )
        if not token_obj:
            return None

        # Check if token is expired and attempt refresh
        if token_obj.expires_at:
            from django.utils import timezone
            if token_obj.expires_at <= timezone.now():
                refreshed = _refresh_github_token(token_obj)
                if not refreshed:
                    return None

        return token_obj.token
    except Exception as exc:
        if "no such table" in str(exc) or "socialaccount_socialaccount" in str(exc):
            return None
        logger.exception("Failed to get GitHub token")
        return None


def _refresh_github_token(token_obj):
    """Use the refresh token to obtain a new GitHub access token.

    Returns True if refresh succeeded, False otherwise.
    Updates token_obj in-place and saves to DB.
    """
    # token_secret stores the refresh token in allauth
    refresh_token = getattr(token_obj, "token_secret", None)
    if not refresh_token:
        logger.warning("No refresh token stored — user must reconnect GitHub")
        return False

    try:
        from allauth.socialaccount.models import SocialApp
        app = SocialApp.objects.filter(provider="github").first()
        if not app:
            logger.error("No GitHub SocialApp configured")
            return False

        resp = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": app.client_id,
                "client_secret": app.secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=10,
        )
        data = resp.json()

        if "access_token" not in data:
            logger.error("GitHub token refresh failed: %s", data.get("error_description", data))
            return False

        # Update stored token
        from datetime import timedelta

        from django.utils import timezone
        token_obj.token = data["access_token"]
        if data.get("refresh_token"):
            token_obj.token_secret = data["refresh_token"]
        expires_in = data.get("expires_in", 28800)  # default 8h
        token_obj.expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        token_obj.save()
        logger.info("GitHub token refreshed successfully for account %s", token_obj.account)
        return True

    except Exception as exc:
        logger.exception("GitHub token refresh error: %s", exc)
        return False


def _get_github_app_repos(user, q: str = "") -> list[dict] | None:
    """Fetch repos from the user's GitHub App installations.

    Returns a list of raw GitHub API repo dicts, or ``None`` if no
    installations exist or the App is not configured.
    """
    try:
        from apps.cloud.models.github_app import GitHubAppInstallation
        from apps.deployments.services.github_app import get_github_app_service

        installations = GitHubAppInstallation.objects.filter(
            user=user,
            status=GitHubAppInstallation.Status.ACTIVE,
        )
        if not installations.exists():
            return None

        svc = get_github_app_service()
        if svc is None:
            return None

        items: list[dict] = []
        for inst in installations:
            repos = svc.list_installation_repos(inst.installation_id)
            items.extend(repos)

        if q:
            q_lower = q.lower()
            items = [r for r in items if q_lower in r.get("full_name", "").lower()]

        return items
    except Exception as exc:
        logger.debug("GitHub App repo fallback failed: %s", exc)
        return None


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_repos(request):
    """
    Return the authenticated user's GitHub repositories.

    Tries the user's OAuth token first; falls back to GitHub App
    installations when the OAuth token is unavailable or expired.

    Query params
    ------------
    q       — Optional search filter (matched against full_name).
    page    — Page number (default 1).
    per_page — Results per page (default 30, max 100).
    sort    — 'updated' (default), 'created', 'pushed', 'full_name'.
    """
    token = _get_github_token(request.user)
    use_app_fallback = token is None

    try:
        page = int(request.query_params.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    per_page = 100
    sort = request.query_params.get("sort", "updated")
    q = request.query_params.get("q", "").strip()

    try:
        if use_app_fallback:
            items = _get_github_app_repos(request.user, q=q)
            if items is None:
                return Response(
                    {
                        "error": "GitHub not connected. Please link your GitHub account first.",
                        "repos": [],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            }

            items = []
            if q:
                # Use the search API for query filtering
                gh = requests.get(
                    "https://api.github.com/search/repositories",
                    headers=headers,
                    params={
                        "q": f"{q} user:@me fork:true",
                        "sort": "updated",
                        "per_page": per_page,
                        "page": page,
                    },
                    timeout=10,
                )
                gh.raise_for_status()
                items = gh.json().get("items", [])
            else:
                # Loop to fetch up to 3 pages (300 repos) to ensure all repos are returned
                for p in range(1, 4):
                    gh = requests.get(
                        "https://api.github.com/user/repos",
                        headers=headers,
                        params={
                            "sort": sort,
                            "direction": "desc",
                            "per_page": per_page,
                            "page": p,
                            "affiliation": "owner,collaborator,organization_member",
                        },
                        timeout=10,
                    )
                    gh.raise_for_status()
                    page_items = gh.json()
                    items.extend(page_items)
                    if len(page_items) < per_page:
                        break

        repos = []
        for r in items:
            if not isinstance(r, dict):
                continue

            repo_data = {
                "full_name": r["full_name"],
                "name": r.get("name") or r["full_name"].split("/")[-1],
                "private": r.get("private", True),
                "default_branch": r.get("default_branch", "main"),
                "html_url": r.get("html_url") or f"https://github.com/{r['full_name']}",
                "clone_url": r.get("clone_url") or f"https://github.com/{r['full_name']}.git",
                "description": r.get("description") or "",
                "language": r.get("language") or "",
                "updated_at": r.get("updated_at"),
                "stargazers_count": r.get("stargazers_count", 0),
            }
            repo_data["category"] = _categorize_repo(repo_data)
            repos.append(repo_data)

        # Build categorized response
        categories = {}
        for repo in repos:
            cat = repo["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(repo)

        # Detect clusters
        clusters = _cluster_repos(repos)

        return Response({
            "repos": repos,
            "categories": categories,
            "clusters": clusters,
            "page": page,
            "per_page": per_page
        })

    except requests.exceptions.HTTPError as exc:
        sc = exc.response.status_code if exc.response is not None else 502
        detail = "GitHub API error"
        if sc == 401:
            detail = "GitHub token expired or revoked. Please reconnect your account."
        logger.warning("GitHub API error %s: %s", sc, exc)
        return Response({"error": detail, "repos": []}, status=sc)
    except Exception as exc:
        logger.exception("Failed to fetch GitHub repos")
        return Response(
            {"error": str(exc), "repos": []},
            status=status.HTTP_502_BAD_GATEWAY,
        )


def _categorize_repo(repo: dict) -> str:
    """Heuristic-based categorization of a repository."""
    name = repo["name"].lower()
    desc = repo["description"].lower()
    lang = repo["language"].lower()

    # 1. Intelligence / AI
    ai_keywords = {"ai", "intelligence", "grok", "openai", "claude", "gemini", "langchain", "llama", "cognition", "braid"}
    if any(k in name or k in desc for k in ai_keywords):
        return "Intelligence"

    # 2. Frontend
    fe_keywords = {"frontend", "ui", "ux", "dashboard", "portal", "nextjs", "react", "vue", "svelte", "tailwind", "css", "html", "website"}
    if any(k in name or k in desc for k in fe_keywords) or lang in {"typescript", "javascript", "css", "html"}:
        # Sub-check: if it has "backend" or "api" it might be fullstack, but let's prioritize Frontend if lang is TS/JS
        if not any(bk in name for bk in ("backend", "api", "core", "server")):
            return "Frontend"

    # 3. Infrastructure / DevOps
    infra_keywords = {"infrastructure", "devops", "docker", "kubernetes", "k8s", "terraform", "ansible", "deployment", "hosting", "cloud"}
    if any(k in name or k in desc for k in infra_keywords) or lang in {"hcl", "shell", "dockerfile"}:
        return "Infrastructure"

    # 4. Core / Backend
    be_keywords = {"backend", "api", "core", "server", "service", "engine", "platform", "os"}
    if any(k in name or k in desc for k in be_keywords) or lang in {"python", "go", "rust", "ruby", "php", "java", "c#", "c++"}:
        return "Core"

    # 5. Utilities
    util_keywords = {"utility", "tools", "cli", "scripts", "helper", "sdk", "library"}
    if any(k in name or k in desc for k in util_keywords):
        return "Utilities"

    return "Others"


def _cluster_repos(repos: list[dict]) -> list[dict]:
    """Detect groups of repositories with common prefixes."""
    if not repos:
        return []

    prefixes: dict[str, list[dict[str, Any]]] = {}
    for repo in repos:
        name = repo["name"]
        if "-" in name:
            prefix = name.split("-")[0]
            if len(prefix) > 2:
                if prefix not in prefixes:
                    prefixes[prefix] = []
                prefixes[prefix].append(repo)

    clusters = []
    for prefix, group in prefixes.items():
        if len(group) >= 3:  # Only count as a cluster if 3+ repos share prefix
            clusters.append({
                "name": prefix.upper(),
                "count": len(group),
                "repos": [r["full_name"] for r in group]
            })
    return sorted(clusters, key=lambda x: x["count"], reverse=True)


def _get_app_token_for_repo(user, repo: str) -> str | None:
    """Get an installation token for *repo* via GitHub App, or ``None``."""
    try:
        from apps.deployments.services.github_app import (
            get_github_app_service,
            get_installation_for_repo,
        )
        installation = get_installation_for_repo(repo)
        if installation and installation.user_id == user.id:
            svc = get_github_app_service()
            if svc:
                return svc.get_installation_token_for_id(installation.installation_id)
    except Exception as exc:
        logger.debug("App token fallback failed for %s: %s", repo, exc)
    return None


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_branches(request):
    """Return branches for a specific repository."""
    repo = request.query_params.get("repo")
    if not repo:
        return Response({"error": "repo parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

    token = _get_github_token(request.user)
    auth_headers = None
    if token:
        auth_headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
    else:
        app_token = _get_app_token_for_repo(request.user, repo)
        if app_token:
            from apps.deployments.services.github_app import get_github_app_service
            svc = get_github_app_service()
            if svc:
                auth_headers = svc._auth_headers(app_token)

    if auth_headers is None:
        return Response({"error": "GitHub not connected"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        gh = requests.get(
            f"https://api.github.com/repos/{repo}/branches",
            headers=auth_headers,
            params={"per_page": 100},
            timeout=10,
        )
        gh.raise_for_status()
        return Response(gh.json())
    except Exception as exc:
        logger.warning("Failed to fetch branches for %s: %s", repo, exc)
        return Response({"error": "Failed to fetch branches"}, status=status.HTTP_502_BAD_GATEWAY)


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_commits(request):
    """Return recent commits for a specific repository and branch."""
    repo = request.query_params.get("repo")
    branch = request.query_params.get("branch")

    if not repo or not branch:
        return Response({"error": "repo and branch parameters are required"}, status=status.HTTP_400_BAD_REQUEST)

    token = _get_github_token(request.user)
    auth_headers = None
    if token:
        auth_headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
    else:
        app_token = _get_app_token_for_repo(request.user, repo)
        if app_token:
            from apps.deployments.services.github_app import get_github_app_service
            svc = get_github_app_service()
            if svc:
                auth_headers = svc._auth_headers(app_token)

    if auth_headers is None:
        return Response({"error": "GitHub not connected"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        gh = requests.get(
            f"https://api.github.com/repos/{repo}/commits",
            headers=auth_headers,
            params={"sha": branch, "per_page": 30},
            timeout=10,
        )
        gh.raise_for_status()
        return Response(gh.json())
    except Exception as exc:
        logger.warning("Failed to fetch commits for %s on branch %s: %s", repo, branch, exc)
        return Response({"error": "Failed to fetch commits"}, status=status.HTTP_502_BAD_GATEWAY)


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_default_branch(request):
    """Get the default branch for a repository.

    Uses the GitHub App installation token when available, falling back
    to the user's OAuth token.  Returns ``{"default_branch": "main"}``.
    """
    repo = request.query_params.get("repo")
    if not repo:
        return Response({"error": "repo parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

    # Try GitHub App installation first
    try:
        from apps.deployments.services.github_app import get_github_app_service, get_installation_for_repo
        installation = get_installation_for_repo(repo)
        if installation:
            svc = get_github_app_service()
            if svc:
                token = svc.get_installation_token_for_id(installation.installation_id)
                if token:
                    resp = requests.get(
                        f"https://api.github.com/repos/{repo}",
                        headers=svc._auth_headers(token),
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        return Response({"default_branch": resp.json().get("default_branch", "main")})
    except Exception as exc:
        logger.debug("Failed to get default branch via App: %s", exc)

    # Fall back to user OAuth token
    token = _get_github_token(request.user)
    if not token:
        return Response({"error": "GitHub not connected"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return Response({"default_branch": resp.json().get("default_branch", "main")})
    except Exception as exc:
        logger.warning("Failed to fetch default branch for %s: %s", repo, exc)
        return Response({"error": "Failed to fetch repository info"}, status=status.HTTP_502_BAD_GATEWAY)
