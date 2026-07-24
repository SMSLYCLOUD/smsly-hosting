"""EcosystemGraphBuilder — builds a topology graph of the SMSLY platform infrastructure."""
import logging
import socket
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


def _check_tcp(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, OSError):
        return False


def _check_http(host: str, port: int, path: str = "/", timeout: float = 2.0) -> bool:
    """Return True if an HTTP GET to host:port/path returns a 2xx/3xx status."""
    try:
        url = f"http://{host}:{port}{path}"
        req = Request(url, headers={"User-Agent": "SMSLY-EcosystemHealth/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except (URLError, OSError):
        return False


def _redis_host_port() -> tuple:
    """Extract Redis host and port from Django settings."""
    host = getattr(settings, 'REDIS_HOST', 'redis')
    port = int(getattr(settings, 'REDIS_PORT', 6379))
    return host, port


def _rabbitmq_host_port() -> tuple:
    """Extract RabbitMQ host and port from Django settings."""
    host = getattr(settings, '_RABBITMQ_HOST', 'rabbitmq')
    port = int(getattr(settings, '_RABBITMQ_PORT', 5672))
    return host, port


def _db_host_port() -> tuple:
    """Extract database host and port from Django DATABASE_URL config."""
    db_cfg = settings.DATABASES.get('default', {})
    host = db_cfg.get('HOST', 'db')
    port = int(db_cfg.get('PORT', 5432) or 5432)
    return host, port


def _registry_host_port() -> tuple:
    """Extract registry host and port from settings."""
    from apps.deployments.models.core import PlatformConfig
    url = PlatformConfig.get_config_value("container_registry_url") or getattr(
        settings, 'CONTAINER_REGISTRY_URL', 'registry:5000',
    )
    # Strip http(s):// prefix if present
    url = url.split('://')[-1].rstrip('/')
    host, _, port_part = url.partition(':')
    port = int(port_part) if port_part.isdigit() else 5000
    return host or 'registry', port


def _backend_host_port() -> tuple:
    """Extract backend host and port from settings."""
    host = getattr(settings, 'BACKEND_HOST', 'backend')
    port = int(getattr(settings, 'BACKEND_PORT', 8000))
    return host, port


def _frontend_host_port() -> tuple:
    """Extract frontend host and port from settings."""
    host = getattr(settings, 'FRONTEND_HOST', 'frontend')
    port = int(getattr(settings, 'FRONTEND_PORT', 3000))
    return host, port


def _socket_proxy_host_port() -> tuple:
    """Extract socket-proxy host and port from settings."""
    host = getattr(settings, 'SOCKET_PROXY_HOST', 'socket-proxy')
    port = int(getattr(settings, 'SOCKET_PROXY_PORT', 2375))
    return host, port


class EcosystemGraphBuilder:
    """Builds a topology graph of the entire SMSLY platform infrastructure."""

    # Default service definitions — overridden at runtime with live health checks
    NODE_DEFINITIONS: list[dict[str, Any]] = [
        {
            "id": "internet",
            "type": "external",
            "kind": "EXTERNAL",
            "label": "Internet",
            "health_check": None,
            "metadata": {"role": "External Traffic Source"},
        },
        {
            "id": "caddy",
            "type": "proxy",
            "kind": "PROXY",
            "label": "Caddy",
            "health_check": {"type": "tcp", "host": "caddy", "port": 80},
            "metadata": {"ports": ["80", "443"], "role": "Edge Proxy / TLS Termination"},
        },
        {
            "id": "traefik",
            "type": "proxy",
            "kind": "PROXY",
            "label": "Traefik",
            "health_check": {"type": "tcp", "host": "traefik", "port": 80},
            "metadata": {"ports": ["80", "8080"], "role": "Docker Service Router"},
        },
        {
            "id": "backend",
            "type": "platform",
            "kind": "COMPUTE",
            "label": "Backend (Django)",
            "health_check": {"type": "dynamic", "fn": "_backend_host_port", "path": "/health/live"},
            "metadata": {"ports": ["8000"], "role": "REST API / WebSocket / Admin"},
        },
        {
            "id": "frontend",
            "type": "platform",
            "kind": "COMPUTE",
            "label": "Frontend (Next.js)",
            "health_check": {"type": "dynamic", "fn": "_frontend_host_port"},
            "metadata": {"ports": ["3000"], "role": "Web Dashboard"},
        },
        {
            "id": "celery-default",
            "type": "worker",
            "kind": "WORKER",
            "label": "Celery Default",
            "health_check": None,
            "metadata": {"queue": "celery", "role": "General Tasks"},
        },
        {
            "id": "celery-fast",
            "type": "worker",
            "kind": "WORKER",
            "label": "Celery Fast",
            "health_check": None,
            "metadata": {"queue": "fast", "role": "Heartbeats / Metrics"},
        },
        {
            "id": "celery-deploy",
            "type": "worker",
            "kind": "WORKER",
            "label": "Celery Deploy",
            "health_check": None,
            "metadata": {"queue": "deploy", "role": "Builds / Provisioning"},
        },
        {
            "id": "celery-beat",
            "type": "worker",
            "kind": "WORKER",
            "label": "Celery Beat",
            "health_check": None,
            "metadata": {"role": "Periodic Scheduler"},
        },
        {
            "id": "postgresql",
            "type": "platform_db",
            "kind": "DATABASE",
            "label": "PostgreSQL",
            "health_check": {"type": "dynamic", "fn": "_db_host_port"},
            "metadata": {"ports": ["5432"], "role": "Platform Database"},
        },
        {
            "id": "redis",
            "type": "platform_cache",
            "kind": "CACHE",
            "label": "Redis",
            "health_check": {"type": "dynamic", "fn": "_redis_host_port"},
            "metadata": {"ports": ["6379"], "role": "Cache + Channels Layer"},
        },
        {
            "id": "rabbitmq",
            "type": "broker",
            "kind": "QUEUE",
            "label": "RabbitMQ",
            "health_check": {"type": "dynamic", "fn": "_rabbitmq_host_port"},
            "metadata": {"ports": ["5672"], "role": "Celery Message Broker"},
        },
        {
            "id": "socket-proxy",
            "type": "proxy",
            "kind": "PROXY",
            "label": "Socket Proxy",
            "health_check": {"type": "dynamic", "fn": "_socket_proxy_host_port"},
            "metadata": {"ports": ["2375"], "role": "Docker API Proxy"},
        },
        {
            "id": "registry",
            "type": "registry",
            "kind": "STORAGE",
            "label": "Docker Registry",
            "health_check": {"type": "dynamic", "fn": "_registry_host_port"},
            "metadata": {"ports": ["5000"], "role": "Image Storage"},
        },
        {
            "id": "frps",
            "type": "tunnel",
            "kind": "EXTERNAL",
            "label": "FRP Server",
            "health_check": {"type": "tcp", "host": "frps", "port": 7000},
            "metadata": {"ports": ["7000", "7080"], "role": "Tunnel Relay"},
        },
        {
            "id": "user-containers",
            "type": "service",
            "kind": "COMPUTE",
            "label": "User Containers",
            "health_check": None,
            "metadata": {"role": "Deployed User Apps"},
        },
    ]

    EDGE_DEFINITIONS: list[dict[str, Any]] = [
        {"source": "internet", "target": "caddy", "type": "PROXY_CHAIN", "label": "HTTP/HTTPS", "animated": True},
        {"source": "caddy", "target": "backend", "type": "PROXY_CHAIN", "label": "/api /ws /admin /health"},
        {"source": "caddy", "target": "frontend", "type": "PROXY_CHAIN", "label": "/ (catch-all)"},
        {"source": "caddy", "target": "traefik", "type": "PROXY_CHAIN", "label": "User app domains"},
        {"source": "traefik", "target": "user-containers", "type": "PROXY_CHAIN", "label": "Dynamic routing"},
        {"source": "backend", "target": "postgresql", "type": "DATABASE", "label": "SQL queries"},
        {"source": "backend", "target": "redis", "type": "CACHE", "label": "Cache + Pub/Sub"},
        {"source": "backend", "target": "socket-proxy", "type": "INTERNAL", "label": "Docker API"},
        {"source": "socket-proxy", "target": "user-containers", "type": "INTERNAL", "label": "Container lifecycle"},
        {"source": "backend", "target": "registry", "type": "INTERNAL", "label": "Push/Pull images"},
        {"source": "celery-default", "target": "rabbitmq", "type": "QUEUE", "label": "AMQP consume"},
        {"source": "celery-fast", "target": "rabbitmq", "type": "QUEUE", "label": "AMQP consume"},
        {"source": "celery-deploy", "target": "rabbitmq", "type": "QUEUE", "label": "AMQP consume"},
        {"source": "celery-beat", "target": "rabbitmq", "type": "QUEUE", "label": "Schedule → Publish"},
        {"source": "backend", "target": "rabbitmq", "type": "QUEUE", "label": "Publish tasks"},
        {"source": "celery-default", "target": "postgresql", "type": "DATABASE", "label": "Task results"},
        {"source": "celery-fast", "target": "postgresql", "type": "DATABASE", "label": "Task results"},
        {"source": "celery-deploy", "target": "postgresql", "type": "DATABASE", "label": "Task results"},
        {"source": "celery-deploy", "target": "socket-proxy", "type": "INTERNAL", "label": "Deploy operations"},
        {"source": "celery-deploy", "target": "registry", "type": "INTERNAL", "label": "Build/Push images"},
        {"source": "frps", "target": "caddy", "type": "TUNNEL", "label": "Tunnel vhost"},
    ]

    def build(self) -> dict[str, Any]:
        """Return {nodes: [...], edges: [...]} representing the platform ecosystem."""
        nodes = self._build_nodes()
        edges = [dict(e) for e in self.EDGE_DEFINITIONS]
        return {"nodes": nodes, "edges": edges}

    def _build_nodes(self) -> list[dict[str, Any]]:
        """Build node list with live health status from TCP probes."""
        nodes: list[dict[str, Any]] = []
        for defn in self.NODE_DEFINITIONS:
            status = self._check_health(defn)
            node = {
                "id": defn["id"],
                "type": defn["type"],
                "kind": defn["kind"],
                "label": defn["label"],
                "status": status,
                "metadata": defn.get("metadata", {}),
            }
            nodes.append(node)
        return nodes

    def _check_health(self, defn: dict[str, Any]) -> str:
        """Probe a service and return 'healthy', 'degraded', or 'down'."""
        check = defn.get("health_check")
        if not check:
            return "healthy"  # assume running if we can't probe

        try:
            if check["type"] == "tcp":
                host, port = check["host"], check["port"]
                return "healthy" if _check_tcp(host, port, timeout=1.5) else "down"
            elif check["type"] == "http":
                host, port = check["host"], check["port"]
                path = check.get("path", "/")
                return "healthy" if _check_http(host, port, path, timeout=2.0) else "down"
            elif check["type"] == "dynamic":
                fn_name = check["fn"]
                host, port = globals()[fn_name]()
                return "healthy" if _check_tcp(host, port, timeout=1.5) else "down"
        except Exception as exc:
            logger.debug("Health check failed for %s: %s", defn["id"], exc)
            return "degraded"
