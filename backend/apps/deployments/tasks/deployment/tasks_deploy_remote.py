import logging

logger = logging.getLogger(__name__)

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM
from apps.deployments.models import (
    Deployment,
)
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    log_exhaustive_self_heal_diagnostics,
)

from ..deploy.helpers import _is_traefik_not_ready, _route_misroute_reason  # noqa: F401
from ..remote.core import (  # noqa: F401
    _copy_remote_deployment_fields,
    _handle_remote_deployment,
    _handle_remote_deployment_legacy,
    _poll_remote_deployment,
    _remote_deploy_failed,
    _remote_failure_message,
    _resume_remote_deployment,
    _stop_local_service_container,
)


@shared_task(bind=True, name="apps.deployments.tasks_deploy_remote.self_heal_remote_deployment", max_retries=0, soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1])
def self_heal_remote_deployment(self, deployment_id: str, server_id: str):
    try:
        deployment = Deployment.objects.get(id=deployment_id)
    except Deployment.DoesNotExist:
        logger.warning("Self-heal: deployment %s not found", deployment_id)
        return

    try:
        from apps.deployments.models.core import ManagedServer
        server = ManagedServer.objects.get(id=server_id)
    except Exception as exc:
        logger.warning("Self-heal: server %s not found: %s", server_id, exc)
        return

    if not (server.ssh_key or server.ssh_password):
        logger.info("Self-heal: no SSH credentials for server %s", server.name)
        return

    append_log(deployment, "\n🔧 Self-healing: diagnosing remote node failure...\n")
    broadcast_status(deployment)

    try:
        from apps.deployments.services.self_healing_orchestrator import (
            RecoveryAction,
            SelfHealingOrchestrator,
        )

        orchestrator = SelfHealingOrchestrator(server)
        result = orchestrator.heal_deployment_failure(deployment)

        append_log(deployment, f"[Self-Heal] Action: {result.action_taken.value}\n")
        append_log(deployment, f"[Self-Heal] Success: {result.success}\n")
        append_log(deployment, f"[Self-Heal] Details: {result.details}\n")
        log_exhaustive_self_heal_diagnostics(
            deployment,
            result.action_taken.value if hasattr(result.action_taken, 'value') else str(result.action_taken),
            result.success,
            result.details,
            next_action=result.next_action.value if hasattr(result, 'next_action') and hasattr(result.next_action, 'value') else str(getattr(result, 'next_action', 'None'))
        )

        if result.success:
            append_log(deployment, f"[Self-Heal] Recovery succeeded: {result.action_taken.value}\n")
            append_log(deployment, f"[Self-Heal] Post-recovery status: {result.post_recovery_status}\n")

            if result.next_action:
                append_log(deployment, f"[Self-Heal] Suggested next action: {result.next_action.value}\n")

            deployment.refresh_from_db()
            if deployment.status == Deployment.Status.FAILED:
                deployment.status = Deployment.Status.QUEUED
                deployment.build_logs += "\n[Self-Heal] Retrying deployment after successful recovery...\n"
                deployment.save(update_fields=["status", "build_logs", "updated_at"])
                broadcast_status(deployment)

                try:
                    provider = deployment.service.provider
                    if provider:
                        from .tasks_deploy import enqueue_smart_deploy_task
                        enqueue_smart_deploy_task(
                            deployment_id=str(deployment.id),
                            provider_id=str(provider.id),
                            skip_review=True,
                        )
                        append_log(deployment, "[Self-Heal] Deployment retry queued\n")
                except Exception as exc:
                    append_log(deployment, f"[Self-Heal] Failed to queue retry: {exc}\n")
                    logger.warning("Self-heal retry queue failed: %s", exc)

        elif result.next_action == RecoveryAction.ESCALATE_TO_AI:
            append_log(deployment, "[Self-Heal] Escalating to system intelligence (AI)...\n")

            try:
                diagnostics = orchestrator.run_full_diagnostics(deployment)
                ai_result = orchestrator.escalate_to_ai(deployment, diagnostics)

                if ai_result.get("success"):
                    append_log(deployment, "[Self-Heal] AI analysis received\n")
                    commands = ai_result.get("suggested_commands", [])
                    if commands:
                        append_log(deployment, "[Self-Heal] AI suggested commands:\n")
                        for cmd in commands[:5]:
                            append_log(deployment, f"  CMD: {cmd}\n")

                    deployment.ai_diagnosis = ai_result.get("ai_response", "")[:2000]
                    deployment.save(update_fields=["ai_diagnosis", "updated_at"])
                else:
                    append_log(deployment, f"[Self-Heal] AI escalation failed: {ai_result.get('error', 'unknown')}\n")

            except Exception as exc:
                append_log(deployment, f"[Self-Heal] AI escalation error: {exc}\n")
                logger.warning("Self-heal AI escalation failed: %s", exc)

        else:
            append_log(deployment, f"[Self-Heal] Recovery failed: {result.details}\n")
            if result.next_action:
                append_log(deployment, f"[Self-Heal] Suggested next action: {result.next_action.value}\n")

        heal_log = orchestrator.get_heal_log()
        if heal_log:
            append_log(deployment, "[Self-Heal] Heal log:\n")
            for entry in heal_log[-10:]:
                append_log(deployment, f"  - {entry}\n")

    except Exception as exc:
        append_log(deployment, f"[Self-Heal] Exception: {exc}\n")
        logger.exception("Self-healing task failed for deployment %s", deployment_id)
    finally:
        broadcast_status(deployment)
