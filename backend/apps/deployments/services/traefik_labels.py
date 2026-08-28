# pylint: disable=line-too-long,too-many-arguments,R0917
"""Traefik Labels module."""
"""
Traefik Label Helpers for SMSLY Hosting.

Generates Docker container labels for Traefik routing configuration.
Enables automatic service discovery, SSL termination, and custom domains.
"""


import os
import re


def _normalize_health_path(path: str) -> str:
    value = str(path or "/").strip()
    if not value.startswith("/"):
        value = f"/{value}"
    value = re.sub(r'[^a-zA-Z0-9._/\-]', '', value)
    if not value:
        value = "/"
    return value


def _health_paths(primary_path: str | None) -> list[str]:
    """Same fallback logic as Docker health check in cloud/adapters/local.py"""
    values = []
    if primary_path:
        values.append(_normalize_health_path(primary_path))

    raw = os.environ.get(
        "DOCKER_HEALTHCHECK_FALLBACK_PATHS",
        "/,/health,/healthz,/ready,/live,/status,/api/health",
    )
    for chunk in str(raw).split(","):
        path = _normalize_health_path(chunk.strip())
        if path and path not in values:
            values.append(path)

    if not values:
        values = ["/"]
    return values


def generate_traefik_labels(
    service_name: str,
    domain: str | None = None,
    internal_port: int = 8000,
    enable_tls: bool = True,
    rate_limit_avg: int = 100,
    rate_limit_burst: int = 200,
    health_check_path: str | None = None,
) -> dict[str, str]:
    """
    Generate Traefik labels for a deployed service container.

    Args:
        service_name: Unique service identifier (used in router names)
        domain: Public domain (e.g., "myapp.smsly.cloud").
                If None, uses subdomain pattern: {service_name}.apps.smsly.cloud
        internal_port: Port the container listens on
        enable_tls: Whether to enable SSL via Let's Encrypt
        rate_limit_avg: Average requests per second allowed
        rate_limit_burst: Maximum burst requests allowed
        health_check_path: Primary health check path (e.g., "/health").
                           Falls back to common paths if not specified.
    """
    # Sanitize service name for use in router names
    router_name = service_name.replace("-", "_").replace(".", "_").lower()

    # Default to subdomain if no custom domain specified
    if not domain:
        domain = f"{service_name}.apps.smsly.cloud"

    # Use same fallback logic as Docker health check
    hc_paths = _health_paths(health_check_path)
    hc_path_primary = hc_paths[0] if hc_paths else health_check_path or "/"

    labels = {
        # Enable Traefik for this container
        "traefik.enable": "true",

        # HTTP Router configuration
        # NOTE: Always use the 'web' entrypoint because Caddy handles SSL
        # termination in production and forwards plain HTTP to Traefik:8081.
        # Traefik does NOT have a 'websecure' entrypoint in production.
        f"traefik.http.routers.{router_name}.rule": f"Host(`{domain}`)",
        f"traefik.http.routers.{router_name}.entrypoints": "web",
        f"traefik.http.routers.{router_name}.service": f"{router_name}-service",

        # Load balancer configuration
        f"traefik.http.services.{router_name}-service.loadbalancer.server.port": str(internal_port),

        # Health check — use the primary path (Traefik health checks take a single path)
        f"traefik.http.services.{router_name}-service.loadbalancer.healthcheck.path": hc_path_primary,
        f"traefik.http.services.{router_name}-service.loadbalancer.healthcheck.interval": "20s",
        f"traefik.http.services.{router_name}-service.loadbalancer.healthcheck.timeout": "8s",
    }

    # NOTE: TLS labels removed — Caddy handles SSL termination in production.
    # Traefik only listens on the 'web' entrypoint (port 80) behind Caddy.

    # Middlewares chain
    middlewares = [f"{router_name}-ratelimit", f"{router_name}-headers"]
    labels[f"traefik.http.routers.{router_name}.middlewares"] = ",".join(
        middlewares)

    # Rate limiting middleware
    labels[f"traefik.http.middlewares.{router_name}-ratelimit.ratelimit.average"] = str(
        rate_limit_avg)
    labels[f"traefik.http.middlewares.{router_name}-ratelimit.ratelimit.burst"] = str(
        rate_limit_burst)
    labels[f"traefik.http.middlewares.{router_name}-ratelimit.ratelimit.period"] = "1s"

    # Security headers middleware
    labels[f"traefik.http.middlewares.{router_name}-headers.headers.stsSeconds"] = "31536000"
    labels[f"traefik.http.middlewares.{router_name}-headers.headers.stsIncludeSubdomains"] = "true"
    labels[f"traefik.http.middlewares.{router_name}-headers.headers.stsPreload"] = "true"
    labels[f"traefik.http.middlewares.{router_name}-headers.headers.contentTypeNosniff"] = "true"
    labels[f"traefik.http.middlewares.{router_name}-headers.headers.frameDeny"] = "true"
    labels[f"traefik.http.middlewares.{router_name}-headers.headers.browserXssFilter"] = "true"

    return labels


def generate_preview_labels(
    parent_service_name: str,
    pr_number: int,
    internal_port: int = 8000,
) -> dict[str, str]:
    """
    Generate Traefik labels for a PR preview environment.

    Preview environments get domains like: pr-123.myapp.preview.smsly.cloud
    """
    preview_name = f"{parent_service_name}-pr-{pr_number}"
    domain = f"pr-{pr_number}.{parent_service_name}.preview.smsly.cloud"

    return generate_traefik_labels(
        service_name=preview_name,
        domain=domain,
        internal_port=internal_port,
        enable_tls=True,
        # Lower rate limits for previews
        rate_limit_avg=50,
        rate_limit_burst=100,
    )


def labels_to_docker_args(labels: dict[str, str]) -> str:
    """
    Convert labels dict to docker run --label arguments.

    Returns:
        String like: --label "key1=value1" --label "key2=value2"
    """
    return " ".join([f'--label "{k}={v}"' for k, v in labels.items()])


def labels_to_compose_dict(labels: dict[str, str]) -> list:
    """
    Convert labels dict to docker-compose labels format.

    Returns:
        List like: ["key1=value1", "key2=value2"]
    """
    return [f"{k}={v}" for k, v in labels.items()]
