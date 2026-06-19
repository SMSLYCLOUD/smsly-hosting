import logging
from django.utils import timezone
from apps.core.services.audit_service import AuditService

logger = logging.getLogger(__name__)

# Define allowed transitions to prevent invalid state jumps
ALLOWED_DEPLOYMENT_TRANSITIONS = {
    "pending_approval": ["approved", "cancelled"],
    "approved": ["queued", "cancelled"],
    "queued": ["deploying", "cancelled"],
    "deploying": ["running", "failed", "cancelled"],
    "running": ["rollback_started", "stopping", "failed", "cancelled"],
    "failed": ["rollback_started", "cancelled"],
    "stopping": ["stopped", "failed"],
    "stopped": ["deploying", "cancelled", "running"],
    "rollback_started": ["rollback_running", "rollback_failed", "rollback_succeeded"],
    "rollback_running": ["rollback_failed", "rollback_succeeded"],
}

class DeploymentStateManager:
    """Centralized manager for safe deployment state transitions."""

    @classmethod
    def transition(cls, deployment, target_status: str, actor="system", reason="", metadata=None):
        if metadata is None:
            metadata = {}

        current_status = deployment.status

        # 1. Validate Transition
        allowed_targets = ALLOWED_DEPLOYMENT_TRANSITIONS.get(current_status, [])
        if target_status != current_status and target_status not in allowed_targets:
            raise ValueError(
                f"Invalid deployment state transition: {current_status} -> {target_status}. "
                f"Allowed transitions from {current_status}: {allowed_targets}."
            )

        # 2. Update State
        deployment.status = target_status
        deployment.save(update_fields=['status'])

        # 3. Write Audit Log
        AuditService.log(
            action=f"deployment_transitioned_to_{target_status}",
            actor=actor,
            target=f"deployment_{deployment.id}",
            metadata={
                "previous_state": current_status,
                "new_state": target_status,
                "reason": reason,
                **metadata
            }
        )

        return {"ok": True, "status": target_status}
