"""Shared Docker client factory.

L-3 fix: Always respects DOCKER_HOST env var to ensure all Docker SDK
calls go through the socket-proxy service rather than hitting the raw
Docker socket directly.
"""
import os
import docker

# Default timeout for normal Docker operations (builds, container ops).
# docker-py defaults to 60s which is too short for many operations.
_DEFAULT_TIMEOUT = 600  # 10 minutes

# Extended timeout for long-running exec sessions (interactive terminals).
# These connections can be idle for minutes between user keystrokes.
_EXEC_TIMEOUT = 3600  # 1 hour


def get_docker_client(**kwargs):
    """Return a Docker client that respects DOCKER_HOST.

    Accepts the same keyword arguments as docker.DockerClient (e.g.
    ``timeout``).  Falls back to the default ``/var/run/docker.sock``
    only when DOCKER_HOST is not set.
    """
    docker_host = os.environ.get('DOCKER_HOST', 'unix:///var/run/docker.sock')
    kwargs.setdefault('timeout', _DEFAULT_TIMEOUT)
    return docker.DockerClient(base_url=docker_host, **kwargs)


def get_docker_exec_client():
    """Return a Docker client tuned for long-running exec sessions.

    Uses a much longer HTTP timeout so terminal sessions don't get
    killed by the Docker SDK's connection timeout during idle periods.
    """
    docker_host = os.environ.get('DOCKER_HOST', 'unix:///var/run/docker.sock')
    return docker.DockerClient(base_url=docker_host, timeout=_EXEC_TIMEOUT)
