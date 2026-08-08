from __future__ import annotations

import logging
import os
import shutil
from contextlib import suppress
from typing import Any

import docker
from django.utils import timezone

from apps.deployments.models import Deployment

logger = logging.getLogger(__name__)

try:
    from apps.intelligence.models import AIProviderSettings as _AIProviderSettings
except (ImportError, RuntimeError):
    _AIProviderSettings = None
AIProviderSettings = _AIProviderSettings


def _handle_failure(_task: Any, deployment: Deployment | None, error_msg: str, reason: str) -> None:
    logger.error("%s: %s", reason, error_msg)

    if deployment:
        deployment.refresh_from_db()
        if deployment.status != 'CANCELLED':
            deployment.status = 'FAILED'
            deployment.finished_at = timezone.now()

            with suppress(Exception):
                from .tasks_commit_status import update_commit_status
                update_commit_status.delay(
                    str(deployment.id), 'failure', f'{reason}: {error_msg}'[:140]
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
                            state='failure',
                            description=f'{reason}'[:140],
                        )

            with suppress(Exception):
                if deployment.service.is_preview and deployment.service.pr_number:
                    from apps.deployments.services.github_pr_comment import (
                        post_pr_comment, build_preview_comment,
                    )
                    from apps.deployments.tasks.cicd.tasks_commit_status import _extract_repo_path
                    repo_name = _extract_repo_path(deployment.service.repository_url or '')
                    if repo_name:
                        body = build_preview_comment(
                            service_name=deployment.service.name,
                            url='',
                            branch=deployment.service.branch,
                            commit_sha=deployment.commit_hash,
                            pr_number=deployment.service.pr_number,
                            status='failed',
                        )
                        comment_id = deployment.service.last_pr_comment_id
                        new_id = post_pr_comment(
                            repo_name, deployment.service.pr_number, body,
                            comment_id=comment_id,
                        )
                        if new_id and new_id != comment_id:
                            deployment.service.last_pr_comment_id = new_id
                            deployment.service.save(update_fields=['last_pr_comment_id'])

            safe_reason = str(reason).replace('\x00', '')
            safe_msg = str(error_msg).replace('\x00', '')

            # Redact secret values from build logs before persisting
            try:
                from apps.deployments.utils.files import redact_values
                _secret_vals = []
                if deployment.service:
                    _secret_vals = [
                        str(v) for v in [
                            getattr(deployment.service, 'environment', {}),
                        ]
                        if isinstance(v, str) and len(v) >= 4
                    ]
                safe_msg = redact_values(safe_msg, _secret_vals)
            except Exception:
                pass

            deployment.build_logs += f"\n✗ {safe_reason}: {safe_msg}\n"
            deployment.save()
            from apps.deployments.utils import broadcast_status
            broadcast_status(deployment)

            try:
                if deployment.green_container_id or deployment.container_id:
                    client = docker.from_env()
                    c_ids_to_remove = [id for id in [deployment.green_container_id, deployment.container_id] if id]
                    cleaned_any = False
                    for c_id in set(c_ids_to_remove):
                        try:
                            container = client.containers.get(c_id)
                            container.remove(force=True)
                            logger.info(f"Cleaned up orphaned container {c_id} for failed deployment {deployment.id}")
                            cleaned_any = True
                        except docker.errors.NotFound:
                            pass
                        except docker.errors.DockerException as e:
                            logger.warning("Failed to cleanup container %s: %s", c_id, e)
                    if cleaned_any:
                        deployment.build_logs += "\n🧹 Cleaned up orphaned container resources.\n"
                        deployment.save(update_fields=['build_logs'])
                from apps.deployments.services.pipeline import _get_builds_root
                build_dir = os.path.join(
                    _get_builds_root(),
                    f"svc_{deployment.service_id}",
                )
                if os.path.isdir(build_dir):
                    shutil.rmtree(build_dir, ignore_errors=True)
                    logger.info("Cleaned up build directory %s for failed deployment %s", build_dir, deployment.id)
            except (docker.errors.DockerException, OSError) as e:
                logger.warning("Docker client error during failure cleanup: %s", e)

            try:
                from apps.core.tasks.alerts import alert_user_task
                alert_user_task.delay(deployment_id=str(deployment.id), error_message=f"{reason}: {error_msg}")
            except Exception as alert_err:
                logger.debug("Failed to queue deployment failure alert: %s", alert_err)

            try:
                from apps.deployments.services.error_resolver import (
                    diagnose_runtime_logs,
                )
                diagnose_runtime_logs(
                    deployment.build_logs,
                    service=deployment.service,
                    deployment=deployment,
                    auto_apply=True,
                )
            except Exception as e:
                logger.debug("Pattern resolver failed: %s", e)

            try:
                from apps.deployments.tasks.ai.tasks_ai import analyze_failure_task
                analyze_failure_task.delay(deployment_id=str(deployment.id))
            except ImportError:
                pass
            except Exception as e:
                logger.debug("Failed to trigger AI failure task: %s", e)

            try:
                from apps.intelligence.jules_fix import jules_fix_deployment_failure
                service = deployment.service
                if not AIProviderSettings:
                    logger.debug("Jules auto-fix skipped: intelligence app not available in agent mode")
                elif not AIProviderSettings.get_solo().jules_api_key:
                    logger.debug("Jules auto-fix skipped: no Jules API key configured")
                elif not service.repository_url:
                    logger.debug("Jules auto-fix skipped: service has no repository_url")
                else:
                    from apps.deployments.services.pipeline import _get_builds_root
                    _builds_root = _get_builds_root()
                    repo_path = os.path.join(_builds_root, f"svc_{service.id}")
                    if not os.path.isdir(repo_path):
                        repo_path = ""

                    jules_fix_deployment_failure.delay(
                        deployment_id=str(deployment.id),
                        logs=deployment.build_logs or error_msg,
                        repo_path=repo_path,
                        repo_url=service.repository_url,
                    )
                    logger.info(
                        "Jules auto-fix triggered for deployment %s (repo=%s)",
                        deployment.id, service.repository_url,
                    )
            except ImportError:
                logger.debug("Jules auto-fix skipped: jules_fix module not available")
            except Exception as e:
                logger.debug("Failed to trigger Jules auto-fix: %s", e)

            try:
                target_server = getattr(deployment, "target_server", None) or getattr(deployment.service, "server", None)
                if target_server and (target_server.ssh_key or target_server.ssh_password):
                    logger.info(
                        "Triggering self-healing for remote deployment %s on server %s",
                        deployment.id, target_server.name,
                    )
                    from .tasks_deploy_remote import self_heal_remote_deployment
                    self_heal_remote_deployment.delay(
                        deployment_id=str(deployment.id),
                        server_id=str(target_server.id),
                    )
            except Exception as e:
                logger.debug("Failed to trigger self-healing: %s", e)

    logger.error("Deployment failed (%s), not retrying: %s", reason, error_msg)
