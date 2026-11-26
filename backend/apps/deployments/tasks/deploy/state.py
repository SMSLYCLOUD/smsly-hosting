from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from django.utils import timezone

from apps.deployments.models import Deployment, Service

logger = logging.getLogger(__name__)


def stamp_deployment_artifact_metadata(
    deployment: Deployment | None,
    service: Service | None = None,
    image_ref: str | None = None,
) -> dict[str, Any]:
    """Snapshot artifact provenance into ``deployment.metadata`` for rollback."""
    if deployment is None:
        return {}
    svc = service or getattr(deployment, 'service', None)
    meta = dict(getattr(deployment, 'metadata', None) or {})
    deploy_type = str(getattr(svc, 'deploy_type', '') or '').upper() if svc else ''

    resolved_image = str(image_ref or '').strip()
    if not resolved_image and svc is not None and not str(meta.get('deployed_image') or '').strip():
        resolved_image = str(getattr(svc, 'docker_image', '') or '').strip()
    if resolved_image:
        meta['deployed_image'] = resolved_image

    if svc is not None:
        if deploy_type == 'UPLOAD':
            repo_url = str(getattr(svc, 'repository_url', '') or '').strip()
            if repo_url:
                meta['repository_url'] = repo_url
        elif deploy_type == 'FUNCTION':
            fn_code = str(getattr(svc, 'function_code', '') or '')
            if fn_code:
                meta['function_code'] = fn_code

    deployment.metadata = meta
    return meta


def restore_deployment_artifact_metadata(
    service: Service,
    target_deployment: Deployment,
) -> dict[str, Any]:
    """Restore service artifact fields from ``target_deployment.metadata`` for manual rollback."""
    target_meta = dict(getattr(target_deployment, 'metadata', None) or {})
    deploy_type = str(getattr(service, 'deploy_type', 'GIT') or 'GIT').upper()
    target_image = str(
        target_meta.get('deployed_image')
        or target_meta.get('docker_image')
        or target_meta.get('image_name')
        or ''
    ).strip()
    target_repo_url = str(target_meta.get('repository_url') or '').strip()
    target_fn_code = str(target_meta.get('function_code') or '')

    svc_updates: list[str] = []
    if (
        target_image
        and not target_image.startswith('compose:')
        and target_image != str(getattr(service, 'docker_image', '') or '').strip()
    ):
        service.docker_image = target_image
        svc_updates.append('docker_image')
    if (
        deploy_type == 'UPLOAD'
        and target_repo_url
        and target_repo_url != str(getattr(service, 'repository_url', '') or '').strip()
    ):
        service.repository_url = target_repo_url
        svc_updates.append('repository_url')
    if (
        deploy_type == 'FUNCTION'
        and target_fn_code
        and target_fn_code != str(getattr(service, 'function_code', '') or '')
    ):
        service.function_code = target_fn_code
        svc_updates.append('function_code')
    if svc_updates:
        svc_updates.append('updated_at')
        service.save(update_fields=svc_updates)

    rollback_meta: dict[str, Any] = {'rollback_target_id': str(target_deployment.id)}
    if target_image:
        rollback_meta['deployed_image'] = target_image
    if target_repo_url:
        rollback_meta['repository_url'] = target_repo_url
    if target_fn_code:
        rollback_meta['function_code'] = target_fn_code
    return rollback_meta


def _mark_deployment_active(deployment: Deployment, target_type: str, host_ip: str, runtime_id: str) -> None:
    deployment.verified_target_type = target_type
    deployment.verified_host_ip = host_ip
    deployment.verified_runtime_id = runtime_id
    deployment.verified_at = timezone.now()


def _post_deploy_success(deployment: Deployment, service: Service, log_line_func: Callable[..., Any] | None = None) -> None:
    with suppress(Exception):
        from apps.deployments.tasks.cicd.tasks_commit_status import update_commit_status
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
    with suppress(Exception):
        # Promote recreates the canonical container — the sidecar's
        # `network_mode: container:<old-id>` dangles on the stopped backup
        # while reporting running+healthy mounts. Rebind it to the live
        # container now instead of serving unmeshed until a beat notices.
        # Best-effort by design: the promotion already succeeded and the
        # app is live — a mesh hiccup must never fail it retroactively.
        from apps.mtls.models import MtlsConfig
        cfg = MtlsConfig.objects.filter(
            service=service, enabled=True, sidecar_enabled=True,
        ).first()
        if cfg is not None:
            from apps.mtls.services.envoy_sidecar import EnvoySidecar
            live_id = (
                getattr(deployment, "container_id", "")
                or getattr(service, "active_runtime_id", "")
            )
            EnvoySidecar.reattach_if_stale(service, live_container_id=live_id)
    with suppress(Exception):
        # Post-promotion seed hook: run the service's seed_command once
        # inside the live container (fresh databases need system rows).
        # Best-effort by design — a seed failure must never fail a
        # promotion that is otherwise healthy.
        from apps.deployments.tasks.deploy import seed as _seed_mod
        _seed_mod.run_post_promote_seed(deployment, service)
    with suppress(Exception):
        from apps.security.tasks import run_post_deploy_dast_task
        run_post_deploy_dast_task.delay(str(service.id), str(deployment.id))
