from __future__ import annotations

import logging
import os
from typing import Any

from celery.result import AsyncResult
from django.core.cache import cache

from apps.deployments.models import Deployment
from apps.deployments.utils import append_log

from .provider import _resolve_provider_for_service

logger = logging.getLogger(__name__)

AUTO_APPROVE_COMMIT_MARKERS = (
    "auto-redeploy",
    "auto-remediation",
    "auto-rollback",
    "auto-restart",
    "[auto-fix]",
    "service restart",
)


def should_skip_review_for_commit_message(message: str) -> bool:
    # SECURITY: commit messages are attacker-controlled (webhook pushes).
    # Marker matching allowed any user to bypass the review gate by wording
    # a commit e.g. 'fix service restart bug'. Trusted internal flows
    # (auto-rollback, self-healing) already pass skip_review=True explicitly,
    # so message-based skipping is redundant AND dangerous. Always review.
    return False


def _current_agent_node_queue() -> str:
    if str(os.environ.get("MODE", "")).strip().lower() != "agent":
        return ""
    queue = str(os.environ.get("SMSLY_NODE_QUEUE", "")).strip()
    if not queue or queue == "deploy":
        logger.warning(
            "Agent mode is running without a dedicated SMSLY_NODE_QUEUE; "
            "falling back to the shared deploy queue."
        )
        return ""
    return queue

def enqueue_smart_deploy_task(
    deployment_id: str,
    provider_id: str,
    skip_review: bool = False,
) -> Any:
    from ..deployment.tasks_deploy import smart_deploy_task
    """
    Enqueue a deployment, using a dedicated node queue on lite agents.

    Full installs and lite agents both use a broker local to the server that
    receives the API request. Lite agents still route API-triggered deploys to
    their per-node queue so only that node's worker consumes them.
    """
    kwargs = {
        "deployment_id": str(deployment_id),
        "provider_id": str(provider_id),
        "skip_review": skip_review,
    }
    queue = _current_agent_node_queue()
    if queue:
        return smart_deploy_task.apply_async(
            kwargs=kwargs,
            queue=queue,
            routing_key=queue,
        )
    return smart_deploy_task.delay(**kwargs)

def recover_stalled_queued_deployments(limit: int = 100) -> dict:
    """
    Re-publish queued deployment tasks after a platform restart/update.

    Automated deployments keep their auto-approval semantics even when the
    original Celery publish was lost during an update.
    """
    results = {"seen": 0, "queued": 0, "skipped": 0, "failed": 0}
    deployments = (
        Deployment.objects.filter(status=Deployment.Status.QUEUED)
        .select_related("service", "service__provider")
        .order_by("created_at")[:limit]
    )
    for deployment in deployments:
        results["seen"] += 1
        try:
            task_state = AsyncResult(str(deployment.id)).state
        except Exception:
            logger.debug("Could not check task state for %s", deployment.id)
            task_state = None
        if task_state in ("STARTED", "RECEIVED", "RETRY"):
            logger.info(
                "Skipping re-queue for %s: task is in state %s",
                deployment.id,
                task_state,
            )
            results["skipped"] += 1
            continue
        provider = cache.get(f"provider_resolve:{deployment.service_id}")
        if provider is None:
            provider = _resolve_provider_for_service(
                deployment.service,
                prefer_local=bool(getattr(deployment, "target_is_local", False)),
            )
            if provider:
                cache.set(f"provider_resolve:{deployment.service_id}", provider, timeout=60)
        if not provider:
            append_log(
                deployment,
                "\n[queue-restore] No active provider available; leaving deployment queued.\n",
            )
            results["skipped"] += 1
            continue

        skip_review = deployment.is_rollback or should_skip_review_for_commit_message(
            deployment.commit_message
        )
        try:
            enqueue_smart_deploy_task(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
                skip_review=skip_review,
            )
            append_log(
                deployment,
                f"\n[queue-restore] Requeued deployment task (skip_review={skip_review}).\n",
            )
            results["queued"] += 1
        except Exception as exc:  # pragma: no cover - broker/runtime failure
            logger.exception(
                "Failed to restore queued deployment task for deployment=%s",
                deployment.id,
            )
            append_log(
                deployment,
                f"\n[queue-restore] Failed to requeue deployment task: {exc}\n",
            )
            results["failed"] += 1
    return results
