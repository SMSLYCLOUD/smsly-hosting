"""Celery task for posting GitHub commit statuses on deployments.

DEPRECATED: Use tasks_commit_status.update_commit_status instead, which
dispatches to the correct provider (GitHub, GitLab, Bitbucket) automatically.
This module is kept for backward compatibility during the transition.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from celery import shared_task

logger = logging.getLogger(__name__)


def _extract_repo_full_name(repository_url: str) -> str | None:
    """Extract 'owner/repo' from a repository URL."""
    try:
        parsed = urlparse(repository_url)
        path_parts = parsed.path.strip("/").replace(".git", "").split("/")
        if len(path_parts) >= 2:
            return f"{path_parts[0]}/{path_parts[1]}"
    except Exception:
        pass
    return None


@shared_task(bind=True, max_retries=2, soft_time_limit=30, time_limit=60)
def update_github_commit_status(
    self,
    deployment_id: str,
    state: str,
    description: str,
    target_url: str = "",
):
    """Post a commit status to GitHub for a deployment.

    Args:
        deployment_id: UUID string of the Deployment.
        state: One of 'pending', 'success', 'failure', 'error'.
        description: Short description (max 140 chars).
        target_url: Optional URL to link from the status.
    """
    try:
        from apps.deployments.models import Deployment
        from apps.deployments.services.github_app import (
            get_github_app_service,
            get_installation_for_repo,
        )

        deployment = Deployment.objects.select_related("service").get(id=deployment_id)
        service = deployment.service

        if not service or not service.repository_url:
            return

        repo_full_name = _extract_repo_full_name(service.repository_url)
        if not repo_full_name:
            return

        commit_sha = deployment.commit_hash
        if not commit_sha or len(commit_sha) < 7:
            return

        # Find an installation that covers this repo
        installation = get_installation_for_repo(repo_full_name)

        if not installation:
            logger.debug(
                "No GitHub App installation covers %s; skipping commit status",
                repo_full_name,
            )
            return

        svc = get_github_app_service()
        if not svc:
            return

        success = svc.create_commit_status(
            installation_id=installation.installation_id,
            repo_full_name=repo_full_name,
            sha=commit_sha,
            state=state,
            description=description[:140],
            context="smsly/deploy",
            target_url=target_url,
        )

        if not success:
            logger.warning(
                "Failed to update commit status for deployment %s", deployment_id
            )

    except Deployment.DoesNotExist:
        logger.warning("Deployment %s not found for commit status update", deployment_id)
    except Exception as exc:
        logger.exception("Error updating GitHub commit status: %s", exc)
        raise self.retry(exc=exc)
