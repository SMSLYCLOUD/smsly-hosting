"""Shared Docker client factory.

L-3 fix: Always respects DOCKER_HOST env var to ensure all Docker SDK
calls go through the socket-proxy service rather than hitting the raw
Docker socket directly.
"""
from __future__ import annotations

import logging
import os

import docker

logger = logging.getLogger(__name__)

# Default timeout for normal Docker operations (builds, container ops).
# docker-py defaults to 60s which is too short for many operations.
_DEFAULT_TIMEOUT = 600  # 10 minutes

# Extended timeout for long-running exec sessions (interactive terminals).
# These connections can be idle for minutes between user keystrokes.
_EXEC_TIMEOUT = 3600  # 1 hour


def _get_fallback_sockets():
    """Return an exhaustive list of fallback Docker socket URLs to try if the primary host fails."""
    sockets = []
    if os.name == 'nt':
        sockets.extend([
            'npipe:////./pipe/docker_engine',
            'tcp://127.0.0.1:2375',
            'tcp://localhost:2375',
        ])
    else:
        sockets.extend([
            'unix:///var/run/docker.sock',
            'unix:///run/docker.sock',
        ])
        if hasattr(os, 'getuid'):
            try:
                user_sock = f"unix:///run/user/{os.getuid()}/docker.sock"
                if user_sock not in sockets:
                    sockets.append(user_sock)
            except Exception as e:
                logger.debug("Failed to detect user Docker socket: %s", e)
    # Cross-platform TCP fallbacks (socket-proxy, docker-in-docker, local TCP)
    for tcp_sock in ['tcp://127.0.0.1:2375', 'tcp://localhost:2375', 'tcp://docker:2375', 'tcp://socket-proxy:2375']:
        if tcp_sock not in sockets:
            sockets.append(tcp_sock)
    return sockets


def _create_resilient_client(primary_url: str, **kwargs):
    """Create a DockerClient, falling back to local sockets if primary_url (like socket-proxy) fails."""
    try:
        return docker.DockerClient(base_url=primary_url, **kwargs)
    except Exception as exc:
        err_str = str(exc).lower()
        if any(term in err_str for term in ['nameresolutionerror', 'connection', 'max retries', 'resolve', 'socket-proxy', 'no such file or directory', 'cannot find the file', 'api version']):
            logger.warning("Primary Docker host '%s' unreachable (%s). Attempting fallback sockets...", primary_url, exc)
            for fallback_url in _get_fallback_sockets():
                if fallback_url == primary_url:
                    continue
                try:
                    client = docker.DockerClient(base_url=fallback_url, **kwargs)
                    logger.info("Successfully connected to Docker via fallback '%s'", fallback_url)
                    return client
                except Exception as fb_exc:
                    logger.debug("Fallback Docker host '%s' failed: %s", fallback_url, fb_exc)
        raise exc


def get_docker_client(**kwargs) -> docker.DockerClient:
    """Return a Docker client that respects DOCKER_HOST with resilient fallback.

    Accepts the same keyword arguments as docker.DockerClient (e.g.
    ``timeout``). Falls back to the local docker socket when DOCKER_HOST
    is not set or when socket-proxy/remote host cannot be reached.
    """
    default_socket = 'npipe:////./pipe/docker_engine' if os.name == 'nt' else 'unix:///var/run/docker.sock'
    docker_host = os.environ.get('DOCKER_HOST', default_socket)
    kwargs.setdefault('timeout', _DEFAULT_TIMEOUT)
    return _create_resilient_client(primary_url=docker_host, **kwargs)


def get_docker_exec_client() -> docker.DockerClient:
    """Return a Docker client tuned for long-running exec sessions.

    Uses a much longer HTTP timeout so terminal sessions don't get
    killed by the Docker SDK's connection timeout during idle periods.
    """
    default_socket = 'npipe:////./pipe/docker_engine' if os.name == 'nt' else 'unix:///var/run/docker.sock'
    docker_host = os.environ.get('DOCKER_HOST', default_socket)
    return _create_resilient_client(primary_url=docker_host, timeout=_EXEC_TIMEOUT)


def from_env(**kwargs) -> docker.DockerClient:
    """Alias for get_docker_client for drop-in compatibility with docker.from_env()."""
    return get_docker_client(**kwargs)


def login_for_image(client: docker.DockerClient, image, scope_obj=None) -> dict | None:
    """docker login for an image's registry host (best-effort).

    Resolves pull credentials via the shared registry_credentials helper
    so worker nodes use master-synced credentials rather than their own
    local ``REGISTRY_PASSWORD``. Returns an ``auth_config`` dict when
    credentials were resolved and login succeeded (so callers can also pass
    ``X-Registry-Auth`` directly on pull), or ``None`` otherwise.
    """
    ref = str(image or '')
    if not ref:
        return None
    try:
        from apps.deployments.services.registry_credentials import (
            resolve_pull_credentials,
        )
        host, user, password = resolve_pull_credentials(ref, scope_obj=scope_obj)
    except Exception:
        return None
    if not host or not user or not password:
        return None
    try:
        client.login(username=user, password=password, registry=host)
        return {'username': user, 'password': password}
    except Exception as exc:
        logger.warning("Docker login for pull failed (%s); trying pull anyway", exc)
        return None


def pull_image_with_auth(client: docker.DockerClient, image: str, scope_obj=None):
    """Authenticate best-effort for *image* and pull via *client*."""
    auth_config = login_for_image(client, image, scope_obj=scope_obj)
    if auth_config:
        return client.images.pull(image, auth_config=auth_config)
    return client.images.pull(image)


# Patch docker.from_env so any direct call across the codebase inherits resilience
_original_from_env = docker.from_env
def _resilient_from_env(**kwargs):
    try:
        return _original_from_env(**kwargs)
    except Exception as exc:
        err_str = str(exc).lower()
        if any(term in err_str for term in ['nameresolutionerror', 'connection', 'max retries', 'resolve', 'socket-proxy', 'no such file or directory', 'api version']):
            logger.warning("docker.from_env() failed (%s). Attempting resilient fallback...", exc)
            for fallback_url in _get_fallback_sockets():
                try:
                    return docker.DockerClient(base_url=fallback_url, **kwargs)
                except Exception as fb_exc:
                    logger.debug("Fallback Docker host '%s' failed: %s", fallback_url, fb_exc)
        raise exc

docker.from_env = _resilient_from_env
