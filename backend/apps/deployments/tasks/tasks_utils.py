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



def _env_int(name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value



def resolve_fast_deploy(service, platform_config=None) -> bool:
    """Resolve whether deploys for this service run the fast path.

    Precedence: per-service ``fast_deploy_enabled`` override first
    (True forces on, False forces off), otherwise the platform-wide
    ``fast_deploy_default``. ``None`` (empty) on the service means
    "inherit the platform default". Never raises — unknown state
    means the safe (full review) path.
    """
    try:
        override = getattr(service, "fast_deploy_enabled", None)
        if override is not None:
            return bool(override)
        if platform_config is None:
            from apps.deployments.models import PlatformConfig
            platform_config = PlatformConfig.load()
        return bool(getattr(platform_config, "fast_deploy_default", False))
    except Exception:
        return False


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
