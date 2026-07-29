from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from django.utils import timezone

from apps.deployments.models import Deployment, Service

logger = logging.getLogger(__name__)


def _mark_deployment_active(deployment: Deployment, target_type: str, host_ip: str, runtime_id: str) -> None:
    deployment.verified_target_type = target_type
    deployment.verified_host_ip = host_ip
    deployment.verified_runtime_id = runtime_id
    deployment.verified_at = timezone.now()


def _post_deploy_success(deployment: Deployment, service: Service, log_line_func: Callable[..., Any] | None = None) -> None:
    with suppress(Exception):
        from .tasks_commit_status import update_commit_status
        update_commit_status.delay(
            str(deployment.id), 'success', 'Deployment active'
        )
    with suppress(Exception):
        if deployment.github_deployment_id:
            from apps.deployments.services.github_app import get_github_app_service, get_installation_for_repo
            from apps.deployments.tasks.cicd.tasks_commit_status import _extract_repo_path
            repo_name = _extract_repo_path(deployment.service.repository_url or '')
            inst = get_installation_for_repo(repo_name) if repo_name else None
            svc = get_github_app_service()
            if inst and svc:
                svc.create_deployment_status(
                    installation_id=inst.installation_id,
                    repo_full_name=repo_name,
                    github_deployment_id=deployment.github_deployment_id,
                    state='success',
                    environment_url=deployment.service.public_domain or '',
                    description='Deployment active',
                )
    with suppress(Exception):
        if deployment.service.is_preview and deployment.service.pr_number:
            from apps.deployments.services.github_pr_comment import (
                post_pr_comment, build_preview_comment,
            )
            from apps.deployments.tasks.cicd.tasks_commit_status import _extract_repo_path
            repo_name = _extract_repo_path(deployment.service.repository_url or '')
            if repo_name:
                preview_url = f"https://{deployment.service.public_domain}" if deployment.service.public_domain else ''
                dashboard_url = f"/services/{deployment.service.id}/deployments/{deployment.id}"
                body = build_preview_comment(
                    service_name=deployment.service.name,
                    url=preview_url,
                    branch=deployment.service.branch,
                    commit_sha=deployment.commit_hash,
                    pr_number=deployment.service.pr_number,
                    status='deployed',
                    dashboard_url=dashboard_url,
                )
                comment_id = deployment.service.last_pr_comment_id
                new_id = post_pr_comment(
                    repo_name, deployment.service.pr_number, body,
                    comment_id=comment_id,
                )
                if new_id and new_id != comment_id:
                    deployment.service.last_pr_comment_id = new_id
                    deployment.service.save(update_fields=['last_pr_comment_id'])
    with suppress(Exception):
        from .caddy import _regenerate_caddyfile
        _regenerate_caddyfile()
