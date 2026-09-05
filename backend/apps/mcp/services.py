"""Lifecycle management for the platform MCP (Model Context Protocol) server.

The MCP server (``apps.mcp.server``) exposes platform operations to AI
assistants. It runs as a standalone SSE process — historically started by
hand via ``python manage.py runmcpserver --sse`` — which meant the
dashboard could only document it, never control it.

This module manages a ``smsly-mcp-server`` container (same backend image,
SSE mode) through the Docker SDK so the frontend can start/stop/restart
it and report live status. Creation is on-demand; nothing starts unless
an operator asks for it.
"""

import logging
import os
import socket

logger = logging.getLogger(__name__)

CONTAINER_NAME = "smsly-mcp-server"
MCP_IMAGE = os.getenv("MCP_SERVER_IMAGE", "smsly-hosting-backend:latest")
MCP_PORT = int(os.getenv("MCP_SERVER_PORT", "8001") or 8001)
MCP_NETWORK = os.getenv("MCP_SERVER_NETWORK", "smsly-net")
MCP_MEM_LIMIT = os.getenv("MCP_SERVER_MEM_LIMIT", "512m")

# Env handling: pass through (almost) everything. Django settings.py
# requires dozens of secrets at import (GATEWAY_SECRET, DB passwords,
# API tokens, ...). A curated allowlist rots every time settings gain a
# required var — the MCP container was DOA for exactly this reason
# (missing GATEWAY_SECRET). The container runs the same backend image at
# the same trust level, so instead we drop only host/container-specific
# runtime noise that must never be inherited.
_ENV_DROP_EXACT = {
    "HOSTNAME",  # must be the MCP container's own (used for self-discovery)
    "HOME",
    "PATH",  # image default; host override could break tool lookup
    "PWD",
    "OLDPWD",
    "SHLVL",
    "_",
    "TERM",
    "HOSTTYPE",
}


def _container_env() -> dict:
    """Build the env dict for the MCP container from the backend's env."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _ENV_DROP_EXACT
    }


def _get_client():
    from apps.cloud.docker_client import get_docker_client
    return get_docker_client()


def _own_networks(client) -> list:
    """Networks of this backend container (best guess for MCP attachment)."""
    try:
        hostname = socket.gethostname()
        me = client.containers.get(hostname)
        me.reload()
        nets = list((me.attrs.get("NetworkSettings") or {}).get("Networks", {}).keys())
        if nets:
            return nets
    except Exception as exc:
        logger.debug("Could not detect own container networks: %s", exc)
    return [MCP_NETWORK]


def _to_status(container) -> dict:
    try:
        container.reload()
    except Exception:
        pass
    attrs = container.attrs or {}
    state = (attrs.get("State") or {})
    networks = list(((attrs.get("NetworkSettings") or {}).get("Networks", {})).keys())
    return {
        "exists": True,
        "running": container.status == "running",
        "status": container.status,
        "container_id": (container.id or "")[:12],
        "image": (attrs.get("Config") or {}).get("Image", ""),
        "started_at": state.get("StartedAt", ""),
        "endpoint": f"http://{CONTAINER_NAME}:{MCP_PORT}/sse",
        "port": MCP_PORT,
        "networks": networks,
    }


def get_status() -> dict:
    """Live status of the managed MCP server container."""
    from apps.mcp import server as server_module

    try:
        client = _get_client()
    except Exception as exc:
        return {
            "exists": False, "running": False,
            "error": f"Docker unavailable: {exc}",
            "sdk_available": getattr(server_module, "_MCP_AVAILABLE", False),
        }
    try:
        container = client.containers.get(CONTAINER_NAME)
    except Exception:
        return {
            "exists": False, "running": False,
            "sdk_available": getattr(server_module, "_MCP_AVAILABLE", False),
        }
    result = _to_status(container)
    result["sdk_available"] = getattr(server_module, "_MCP_AVAILABLE", False)
    return result


def _ensure_container(client):
    """Return the MCP container, creating it on first use."""
    try:
        return client.containers.get(CONTAINER_NAME)
    except Exception:
        pass
    logger.info("Creating MCP server container %s", CONTAINER_NAME)
    networks = _own_networks(client)
    primary = networks[0] if networks else MCP_NETWORK
    container = client.containers.create(
        image=MCP_IMAGE,
        name=CONTAINER_NAME,
        command=["python", "manage.py", "runmcpserver", "--sse",
                 "--host", "0.0.0.0", "--port", str(MCP_PORT)],
        environment=_container_env(),
        network=primary,
        ports={"8001/tcp": ("127.0.0.1", MCP_PORT)},
        labels={"managed_by": "smsly-hosting", "smsly.mcp": "true"},
        restart_policy={"Name": "no"},
        mem_limit=MCP_MEM_LIMIT,
        detach=True,
    )
    for extra in networks[1:]:
        try:
            client.networks.get(extra).connect(container)
        except Exception as exc:
            logger.debug("MCP extra network attach skipped (%s): %s", extra, exc)
    return container


def start() -> dict:
    """Create (first run) and start the MCP server. Returns live status."""
    from apps.mcp import server as server_module
    if not getattr(server_module, "_MCP_AVAILABLE", False):
        raise RuntimeError(
            "MCP SDK not installed in this backend image "
            "(needs 'mcp<2.0.0' for FastMCP support)."
        )
    client = _get_client()
    container = _ensure_container(client)
    container.reload()
    if container.status != "running":
        container.start()
        container.reload()
    logger.info("MCP server started (%s)", container.status)
    return get_status()


def stop() -> dict:
    """Stop the MCP server (container kept for fast restarts)."""
    client = _get_client()
    try:
        container = client.containers.get(CONTAINER_NAME)
    except Exception:
        return get_status()
    try:
        container.stop(timeout=10)
    except Exception as exc:
        logger.debug("MCP stop skipped: %s", exc)
    return get_status()


def restart() -> dict:
    """Restart the MCP server."""
    stop()
    return start()
