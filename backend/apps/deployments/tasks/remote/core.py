"""Remote deployment tasks — delegate deployments to remote servers and poll for status."""
import logging
import random
import time

from django.utils import timezone

from apps.deployments.models import Deployment
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    update_stage,
)

from ..deploy.helpers import _handle_failure, _regenerate_caddyfile

logger = logging.getLogger(__name__)
def _handle_remote_deployment_legacy(deployment, server):
    """Delegate deployment to a remote server and poll for status."""
    from apps.deployments.services.server_guard import ServerGuard

    service = deployment.service
    guard = ServerGuard.check_user_workload_allowed(server)
    if not guard["ok"]:
        _handle_failure(
            None,
            deployment,
            guard["error"]["message"],
            "Placement Guard",
        )
        return

    orchestrator = RemoteOrchestrator(server)

    append_log(deployment, f"🌐 Delegating deployment to remote server: {server.name} ({server.host})\n")
    update_stage(deployment, 'Remote Sync', 'running')

    # 1. Sync Service
    remote_svc_id = orchestrator.sync_service(service)
    if not remote_svc_id:
        _handle_failure(
            None,
            deployment,
            _remote_failure_message(orchestrator, "Failed to sync service to remote server"),
            "Remote Sync Failure",
        )
        return

    orchestrator.sync_env_vars(service, remote_svc_id)
    update_stage(deployment, 'Remote Sync', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')

    # 2. Trigger Deploy
    remote_dep_id = orchestrator.trigger_deploy(deployment, remote_svc_id)
    if not remote_dep_id:
        _handle_failure(
            None,
            deployment,
            _remote_failure_message(orchestrator, "Failed to trigger deployment on remote server"),
            "Remote Deploy Failure",
        )
        return

    append_log(deployment, f"🚀 Remote deployment triggered: {remote_dep_id}\n")

    # 3. Polling Loop
    max_retries = 90  # 15 minutes (10s intervals)
    for _i in range(max_retries):
        time.sleep(10)
        remote_status = orchestrator.poll_deployment(remote_dep_id)
        if not remote_status:
            continue

        status = remote_status.get("status")
        # Update stage info with remote status if available
        if status:
            append_log(deployment, f"[Remote] Status: {status}\n")

        if status == Deployment.Status.ACTIVE:
            deployment.status = Deployment.Status.ACTIVE
            deployment.finished_at = timezone.now()
            deployment.save(update_fields=['status', 'finished_at'])
            update_stage(deployment, 'Remote Deploy', 'success')
            broadcast_status(deployment)

            # Post success commit status to GitHub (non-blocking)
            try:
                from .tasks_commit_status import update_commit_status
                update_commit_status.delay(
                    str(deployment.id), 'success', 'Deployment active'
                )
            except Exception:
                pass

            append_log(deployment, "✅ Remote deployment successful!\n")
            return

        if status in (Deployment.Status.FAILED, Deployment.Status.BUILD_FAILED, Deployment.Status.CANCELLED):
            _handle_failure(None, deployment, f"Remote deployment failed with status: {status}", "Remote Execution Failure")
            return

    _handle_failure(None, deployment, "Remote deployment timed out", "Remote Timeout")

def _remote_failure_message(orchestrator, fallback: str) -> str:
    """Append the last upstream error from RemoteOrchestrator when available."""
    try:
        detail = orchestrator.describe_last_error()
    except Exception:
        detail = ""
    if detail:
        return f"{fallback}: {detail}"
    return fallback

def _stop_local_service_container(service_name: str):
    """
    Proactively stop and remove any local container on the Master VPS.
    Used during remote delegation to prevent 'ghost' containers.
    """
    try:
        import docker

        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()
        try:
            container = client.containers.get(service_name)
            logger.info(f"Stopping ghost container {service_name} on Master VPS...")
            container.stop(timeout=10)
            container.remove(force=True)
            logger.info(f"Successfully removed ghost container {service_name}")
        except docker.errors.NotFound:
            pass
        except Exception as e:
            logger.warning(f"Failed to stop ghost container {service_name}: {e}")
    except Exception as e:
        logger.warning(f"Docker client unavailable on Master: {e}")

def _remote_deploy_failed(deployment, orchestrator, fallback_msg, stage):
    _handle_failure(None, deployment, _remote_failure_message(orchestrator, fallback_msg), stage)

def _handle_remote_deployment(deployment, server, skip_review: bool = False, image_name: str | None = None):
    """Delegate deployment to a remote server and poll for status.

    When ``image_name`` is provided (master pre-built and pushed the image),
    it is forwarded to the remote so that node can skip its own build phase
    and go straight to pull + run (build-agent optimization).
    """
    from apps.deployments.services.server_guard import ServerGuard

    service = deployment.service

    # [FIX] Proactively stop any existing local container on Master VPS
    # if this service is being delegated to a remote node.
    _stop_local_service_container(service.name)

    guard = ServerGuard.check_user_workload_allowed(server)
    if not guard["ok"]:
        _handle_failure(
            None,
            deployment,
            guard["error"]["message"],
            "Placement Guard",
        )
        return

    orchestrator = RemoteOrchestrator(server)

    # Pre-flight: verify remote node API is reachable before delegating.
    # If the backend is down (Traefik 404), attempt SSH auto-heal of
    # the entire docker-compose stack on the remote node.
    preflight = orchestrator.preflight_check_or_heal()
    if not preflight['ok']:
        diagnosis = preflight.get('diagnosis', 'unknown')
        healed_note = ' (SSH auto-heal was attempted)' if preflight.get('healed') else ''
        _handle_failure(
            None,
            deployment,
            f"Remote node {server.name} ({server.host}) is unreachable "
            f"[{diagnosis}]{healed_note}: {preflight['error']}",
            "Remote Node Unhealthy",
        )
        return

    append_log(deployment, f"Delegating deployment to remote server: {server.name} ({server.host})\n")
    update_stage(deployment, 'Remote Sync', 'running')

    remote_svc_id = orchestrator.sync_service(service)
    if not remote_svc_id:
        _remote_deploy_failed(
            deployment,
            orchestrator,
            "Failed to sync service to remote server",
            "Remote Sync Failure",
        )
        return

    update_stage(deployment, 'Remote Sync', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')

    remote_dep_id = orchestrator.trigger_deploy(
        deployment, remote_svc_id, skip_review=skip_review, image_name=image_name,
    )
    if not remote_dep_id:
        _remote_deploy_failed(
            deployment,
            orchestrator,
            "Failed to trigger deployment on remote server",
            "Remote Deploy Failure",
        )
        return

    deployment.remote_deployment_id = remote_dep_id
    deployment.status = Deployment.Status.QUEUED  # Stay queued until follower reports a stage
    deployment.started_at = deployment.started_at or timezone.now()
    deployment.save(update_fields=['remote_deployment_id', 'status', 'started_at', 'updated_at'])
    append_log(deployment, f"Remote deployment triggered: {remote_dep_id}\n")
    _poll_remote_deployment(
        deployment,
        orchestrator,
        remote_dep_id,
        remote_service_id=remote_svc_id,
    )

def _resume_remote_deployment(deployment, server):
    """Approve/resume an existing remote deployment and keep polling it."""
    from apps.deployments.services.server_guard import ServerGuard

    service = deployment.service
    guard = ServerGuard.check_user_workload_allowed(server)
    if not guard["ok"]:
        _handle_failure(
            None,
            deployment,
            guard["error"]["message"],
            "Placement Guard",
        )
        return

    orchestrator = RemoteOrchestrator(server)
    remote_dep_id = deployment.remote_deployment_id
    append_log(deployment, f"Resuming remote deployment: {remote_dep_id}\n")
    update_stage(deployment, 'Remote Approval', 'running')

    remote_svc_id = orchestrator.sync_service(service)
    if remote_svc_id:
        orchestrator.sync_env_vars(service, remote_svc_id)

    payload = {
        "cpu_cores": str(service.cpu_cores),
        "memory_mb": service.memory_mb,
    }
    if not orchestrator.approve_deployment(remote_dep_id, payload=payload):
        _remote_deploy_failed(
            deployment,
            orchestrator,
            "Failed to approve remote deployment",
            "Remote Approval Failure",
        )
        return

    update_stage(deployment, 'Remote Approval', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')
    _poll_remote_deployment(
        deployment,
        orchestrator,
        remote_dep_id,
        remote_service_id=remote_svc_id,
    )

def _copy_remote_deployment_fields(deployment, remote_status: dict):
    """Mirror useful remote deployment fields onto the controller row."""
    update_fields = []
    if remote_status.get("build_logs") and remote_status.get("build_logs") != deployment.build_logs:
        deployment.build_logs = remote_status.get("build_logs") or ""
        update_fields.append("build_logs")
    if remote_status.get("review_summary") and remote_status.get("review_summary") != deployment.review_summary:
        deployment.review_summary = remote_status.get("review_summary") or {}
        update_fields.append("review_summary")
    if remote_status.get("ai_diagnosis") and remote_status.get("ai_diagnosis") != deployment.ai_diagnosis:
        deployment.ai_diagnosis = remote_status.get("ai_diagnosis") or ""
        update_fields.append("ai_diagnosis")
    if remote_status.get("pipeline_stages") and remote_status.get("pipeline_stages") != deployment.pipeline_stages:
        deployment.pipeline_stages = remote_status.get("pipeline_stages") or []
        update_fields.append("pipeline_stages")
    if remote_status.get("vulnerability_report") and remote_status.get("vulnerability_report") != deployment.vulnerability_report:
        deployment.vulnerability_report = remote_status.get("vulnerability_report") or {}
        update_fields.append("vulnerability_report")
    if remote_status.get("runtime_logs_url") and remote_status.get("runtime_logs_url") != deployment.runtime_logs_url:
        deployment.runtime_logs_url = remote_status.get("runtime_logs_url")
        update_fields.append("runtime_logs_url")
    if remote_status.get("commit_hash") and remote_status.get("commit_hash") != deployment.commit_hash:
        deployment.commit_hash = remote_status.get("commit_hash")
        update_fields.append("commit_hash")
    if remote_status.get("commit_message") and remote_status.get("commit_message") != deployment.commit_message:
        deployment.commit_message = remote_status.get("commit_message")
        update_fields.append("commit_message")
    if update_fields:
        update_fields.append("updated_at")
        deployment.save(update_fields=update_fields)

def _poll_remote_deployment(
    deployment,
    orchestrator,
    remote_dep_id,
    remote_service_id=None,
):
    """Poll a delegated deployment until it reaches REVIEW or a terminal state."""
    max_retries = 90  # 15 minutes (10s intervals)
    max_empty_polls = 12  # 2 minutes of unreachable/invalid poll responses.
    empty_polls = 0
    logger.info("Starting polling for remote deployment %s on node %s", remote_dep_id, orchestrator.server.host)
    append_log(deployment, f"[Remote] Initializing poller for remote deployment: {remote_dep_id}\n")

    for i in range(max_retries):
        time.sleep(10 + random.uniform(0, 2))

        if i % 6 == 0:  # Log every 60 seconds
             logger.debug("Polling remote deployment %s (attempt %d/%d)", remote_dep_id, i+1, max_retries)

        remote_status = orchestrator.poll_deployment(remote_dep_id)
        if not remote_status:
            empty_polls += 1
            if empty_polls in (3, 6, 12):
                append_log(
                    deployment,
                    (
                        "[Remote] Status unavailable "
                        f"({empty_polls}/{max_empty_polls}): "
                        f"{_remote_failure_message(orchestrator, 'poll failed')}\n"
                    ),
                )
            if empty_polls >= max_empty_polls:
                _handle_failure(
                    None,
                    deployment,
                    _remote_failure_message(
                        orchestrator,
                        "Remote deployment status could not be fetched",
                    ),
                    "Remote Poll Failure",
                )
                return
            continue
        empty_polls = 0

        status = remote_status.get("status")
        _copy_remote_deployment_fields(deployment, remote_status)
        if status:
            append_log(deployment, f"[Remote] Status: {status}\n")

        if status == Deployment.Status.REVIEW:
            deployment.status = Deployment.Status.REVIEW
            deployment.save(update_fields=['status', 'updated_at'])
            update_stage(deployment, 'Remote Review', 'waiting')
            broadcast_status(deployment)
            append_log(deployment, "Remote deployment paused for review. Approve to continue.\n")
            return

        if status in (
            Deployment.Status.BUILDING,
            Deployment.Status.BACKUP_RUNNING,
            Deployment.Status.MIGRATION_PLANNING,
            Deployment.Status.MIGRATION_RUNNING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        ) and deployment.status != status:
            deployment.status = status
            deployment.save(update_fields=['status', 'updated_at'])
            broadcast_status(deployment)

        # [STALE DETECTION]
        # If the remote is still QUEUED after 3 minutes (18 polls), it might mean
        # the remote worker is stuck or down.
        if status == Deployment.Status.QUEUED and i > 18:
            warning_msg = (
                "[Remote] Warning: Deployment has been QUEUED on the remote node for >3 minutes. "
                "The remote worker may be offline or overloaded.\n"
            )
            if i % 6 == 0: # Only log every minute to avoid spam
                append_log(deployment, warning_msg)
                logger.warning("Remote deployment %s stuck in QUEUED for %d polls", remote_dep_id, i)

        if status == Deployment.Status.ACTIVE:
            # ── MISSION RULE 3: POST-DEPLOYMENT VERIFICATION ──
            # Before accepting ACTIVE status from remote, we MUST verify it exists and is running.
            try:
                status_service_id = remote_service_id or deployment.service.id
                verify_resp = orchestrator._request(
                    method='GET',
                    path=f"/api/v1/services/{status_service_id}/status/",
                    timeout=10,
                )
                if verify_resp and verify_resp.status_code == 200:
                    status_data = verify_resp.json()
                    if status_data.get("status") == "running":
                        remote_container_id = status_data.get("container_id", deployment.container_id)
                        target_type = (
                            "lite_agent"
                            if getattr(orchestrator.server, "is_lite_agent", False)
                            else "remote"
                        )

                        # Persist verified metadata on Deployment
                        deployment.verified_target_type = target_type
                        deployment.verified_host_ip = (
                            getattr(orchestrator.server, "wg_address", None)
                            or orchestrator.server.private_ip
                            or orchestrator.server.host
                        )
                        deployment.verified_runtime_id = remote_container_id
                        deployment.verified_at = timezone.now()

                        deployment.status = Deployment.Status.ACTIVE
                        deployment.finished_at = timezone.now()
                        deployment.save(update_fields=['status', 'finished_at', 'updated_at', 'verified_target_type', 'verified_host_ip', 'verified_runtime_id', 'verified_at'])

                        # Promote to Active Service
                        service = deployment.service
                        service.server = deployment.target_server or orchestrator.server
                        service.active_target_type = target_type
                        service.active_host_ip = deployment.verified_host_ip
                        service.active_runtime_id = remote_container_id
                        service.save(update_fields=['server', 'active_target_type', 'active_host_ip', 'active_runtime_id'])

                        _regenerate_caddyfile()

                        update_stage(deployment, 'Remote Deploy', 'success')
                        broadcast_status(deployment)

                        # Post success commit status to GitHub (non-blocking)
                        try:
                            from .tasks_commit_status import update_commit_status
                            update_commit_status.delay(
                                str(deployment.id), 'success', 'Deployment active'
                            )
                        except Exception:
                            pass

                        append_log(deployment, "Remote deployment completed and VERIFIED successfully.\n")
                        return
                    else:
                        raise ValueError(f"Service status is {status_data.get('status')}, expected running.")
                else:
                     raise ValueError(f"Verification request failed with status {getattr(verify_resp, 'status_code', 'None')}")
            except Exception as e:
                logger.error("Verification failed for deployment %s: %s", deployment.id, e)
                _handle_failure(
                    None,
                    deployment,
                    f"Remote node reported ACTIVE, but post-deployment verification failed: {e}",
                    "Verification Failure"
                )
                return


        if status in (
            Deployment.Status.FAILED,
            Deployment.Status.BUILD_FAILED,
            Deployment.Status.BACKUP_FAILED,
            Deployment.Status.MIGRATION_FAILED,
        ):
            error_detail = remote_status.get("error") or f"Remote deployment failed with status: {status}."
            append_log(deployment, "[Self-Heal] Remote failure detected — triggering self-healing...\n")
            try:
                from ..deployment.tasks_deploy_remote import self_heal_remote_deployment  # noqa: F811
                self_heal_remote_deployment.delay(
                    deployment_id=str(deployment.id),
                    server_id=str(orchestrator.server.id),
                )
            except Exception as exc:
                logger.warning("Failed to trigger self-healing from poller: %s", exc)
            _handle_failure(
                None,
                deployment,
                error_detail,
                "Remote Execution Failure",
            )
            return

        if status == Deployment.Status.CANCELLED:
            deployment.status = Deployment.Status.CANCELLED
            deployment.finished_at = timezone.now()
            deployment.save(update_fields=['status', 'finished_at', 'updated_at'])
            broadcast_status(deployment)
            append_log(deployment, "Remote deployment was cancelled.\n")
            return

    # If we exit the loop without a terminal state
    logger.info("Polling finished for remote deployment %s after %d attempts", remote_dep_id, max_retries)
    intermediate_statuses = (
        Deployment.Status.QUEUED,
        Deployment.Status.BUILDING,
        Deployment.Status.HEALTH_CHECK,
        Deployment.Status.DEPLOYING,
        Deployment.Status.MIGRATION_RUNNING,
        Deployment.Status.MIGRATION_PLANNING,
        Deployment.Status.BACKUP_RUNNING,
        Deployment.Status.REVIEW,
    )
    if deployment.status in intermediate_statuses:
        append_log(
            deployment,
            f"\n[Remote] Polling timed out after 15 minutes while in {deployment.status} state. "
            "The deployment may still be running on the remote node.\n"
        )
        deployment.status = Deployment.Status.FAILED
        deployment.error = "Remote deployment poller timed out. Check the remote node directly for actual status."
        deployment.finished_at = timezone.now()
        deployment.save(update_fields=['status', 'error', 'finished_at', 'updated_at'])
        update_stage(deployment, 'Remote Deploy', 'failed')
        broadcast_status(deployment)

