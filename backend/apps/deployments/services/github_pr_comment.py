"""GitHub PR comment service for preview deployments.

Posts and updates PR comments with preview deployment URLs, build status,
and links to logs — the same UX Railway and Coolify provide.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


def _get_token_and_repo(repo_full_name: str):
    """Resolve an installation token and validate the repo exists."""
    from apps.deployments.services.github_app import (
        get_github_app_service,
        get_installation_for_repo,
    )

    installation = get_installation_for_repo(repo_full_name)
    if not installation:
        return None, None, None

    svc = get_github_app_service()
    if not svc:
        return None, None, None

    token = svc.get_installation_token_for_id(installation.installation_id)
    if not token:
        return None, None, None

    return svc, token, installation


def post_pr_comment(
    repo_full_name: str,
    pr_number: int,
    body: str,
    comment_id: int | None = None,
) -> int | None:
    """Create or update a PR comment. Returns the comment_id.

    If *comment_id* is provided the existing comment is edited in-place
    (Railway-style single-comment updates). Otherwise a new comment is
    created.
    """
    svc, token, installation = _get_token_and_repo(repo_full_name)
    if not svc or not token:
        return None

    headers = svc._auth_headers(token)

    try:
        if comment_id:
            resp = requests.patch(
                f"https://api.github.com/repos/{repo_full_name}/issues/comments/{comment_id}",
                headers=headers,
                json={"body": body},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info(
                    "github_pr_comment: updated comment %s on %s#%s",
                    comment_id, repo_full_name, pr_number,
                )
                return comment_id
            logger.warning(
                "github_pr_comment: patch failed (%s), falling back to new comment",
                resp.status_code,
            )

        resp = requests.post(
            f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments",
            headers=headers,
            json={"body": body},
            timeout=10,
        )
        if resp.status_code == 201:
            new_id = resp.json().get("id")
            logger.info(
                "github_pr_comment: created comment %s on %s#%s",
                new_id, repo_full_name, pr_number,
            )
            return new_id

        logger.error(
            "github_pr_comment: post failed (status %s): %s",
            resp.status_code, resp.text[:200],
        )
    except Exception:
        logger.exception(
            "github_pr_comment: error posting comment on %s#%s",
            repo_full_name, pr_number,
        )
    return None


def delete_pr_comment(repo_full_name: str, comment_id: int) -> bool:
    """Delete a PR comment by ID."""
    svc, token, _ = _get_token_and_repo(repo_full_name)
    if not svc or not token:
        return False

    try:
        resp = requests.delete(
            f"https://api.github.com/repos/{repo_full_name}/issues/comments/{comment_id}",
            headers=svc._auth_headers(token),
            timeout=10,
        )
        return resp.status_code == 204
    except Exception:
        logger.exception(
            "github_pr_comment: error deleting comment %s on %s",
            comment_id, repo_full_name,
        )
    return False


# ── Markdown builders ────────────────────────────────────────────────────────

_STATUS_EMOJI = {
    "building": "🔨",
    "deployed": "✅",
    "active": "✅",
    "failed": "❌",
    "destroyed": "🗑️",
    "pending": "⏳",
}


def build_preview_comment(
    service_name: str,
    url: str,
    branch: str,
    commit_sha: str,
    pr_number: int,
    status: str = "building",
    dashboard_url: str = "",
) -> str:
    """Return a Markdown-formatted PR comment body for a preview deployment."""
    emoji = _STATUS_EMOJI.get(status, "🚀")
    status_label = status.title()

    lines = [
        f"## {emoji} Preview Deployment — `{service_name}`\n",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Status** | {status_label} |",
        f"| **URL** | [{url}]({url}) |",
        f"| **Branch** | `{branch}` |",
        f"| **Commit** | `{commit_sha[:7]}` |",
        f"| **PR** | #{pr_number} |",
    ]
    if dashboard_url:
        lines.append(f"| **Dashboard** | [View Logs]({dashboard_url}) |")
    lines.append("")
    return "\n".join(lines)


def build_preview_destroyed_comment(
    service_name: str,
    pr_number: int,
) -> str:
    """Return a Markdown body for a destroyed preview."""
    return (
        f"## 🗑️ Preview Destroyed — `{service_name}`\n\n"
        f"Preview environment for PR #{pr_number} has been removed.\n"
    )
