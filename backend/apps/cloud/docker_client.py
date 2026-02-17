"""Shared Docker client factory.

L-3 fix: Always respects DOCKER_HOST env var to ensure all Docker SDK
calls go through the socket-proxy service rather than hitting the raw
Docker socket directly.
"""
import os
import docker


def get_docker_client(**kwargs):
    """Return a Docker client that respects DOCKER_HOST.

    Accepts the same keyword arguments as docker.DockerClient (e.g.
    ``timeout``).  Falls back to the default ``/var/run/docker.sock``
    only when DOCKER_HOST is not set.
    """
    docker_host = os.environ.get('DOCKER_HOST', 'unix:///var/run/docker.sock')
    return docker.DockerClient(base_url=docker_host, **kwargs)
