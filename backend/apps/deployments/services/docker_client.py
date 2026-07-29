"""Shared Docker client to avoid repeated connection creation."""
import threading
import docker

_local = threading.local()

def get_docker_client():
    """Get a thread-local Docker client."""
    if not hasattr(_local, 'client') or _local.client is None:
        _local.client = docker.from_env()
    return _local.client
