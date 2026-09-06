"""Keep the managed MCP server container running (auto-start)."""
import logging

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)


@shared_task(name="apps.mcp.tasks.ensure_mcp_server_running", soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1], max_retries=0)
def ensure_mcp_server_running() -> dict:
    """Beat task (5m): recreate the MCP container if it is missing.

    Only the missing case is healed: an operator-stopped container is
    left alone (explicit stop is respected), and Docker's unless-stopped
    policy already covers daemon/host restarts. This converges back to
    running after image rebuilds, `docker rm`, or first boot — without
    ever overriding a deliberate stop.
    """
    from apps.mcp import server as server_module
    from apps.mcp import services as mcp_services

    if not getattr(mcp_services, "MCP_AUTOSTART", True):
        return {"status": "disabled"}
    if not getattr(server_module, "_MCP_AVAILABLE", False):
        return {"status": "skipped", "reason": "mcp SDK not installed"}
    try:
        payload = mcp_services.get_status()
    except Exception as exc:
        logger.warning("MCP ensure: status check failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    if payload.get("running"):
        return {"status": "already_running", "container_id": payload.get("container_id")}
    if payload.get("exists"):
        return {"status": "stopped_left_alone"}
    try:
        started = mcp_services.start()
        logger.info("MCP ensure: started container %s", started.get("container_id"))
        return {"status": "started", "container_id": started.get("container_id")}
    except Exception as exc:
        logger.warning("MCP ensure: start failed: %s", exc)
        return {"status": "error", "error": str(exc)}
