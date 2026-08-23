import logging
import os

logger = logging.getLogger(__name__)
AUTO_APPROVE_COMMIT_MARKERS = (
    "auto-redeploy",
    "auto-remediation",
    "auto-rollback",
    "auto-restart",
    "[auto-fix]",
    "service restart",
)

def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, str(default))).strip().lower()
    return raw in {"1", "true", "yes", "on"}



def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)



def should_skip_review_for_commit_message(message: str) -> bool:
    """Return True for system-created deployments that must not pause at REVIEW."""
    # SECURITY: commit messages are attacker-controlled (webhook pushes).
    # Marker matching allowed any user to bypass the review gate by wording
    # a commit e.g. 'fix service restart bug'. Trusted internal flows
    # (auto-rollback, self-healing) already pass skip_review=True explicitly,
    # so message-based skipping is redundant AND dangerous. Always review.
    return False



def _current_agent_node_queue() -> str:
    """Return this lite agent's dedicated deploy queue, if running as an agent."""
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
