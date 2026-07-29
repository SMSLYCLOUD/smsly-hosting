"""Deployment helper functions — re-export hub for sub-modules."""
from __future__ import annotations

import importlib as _importlib
import logging

logger = logging.getLogger(__name__)

from .env import _env_bool, _env_int  # noqa: F401 — needed by build_compose, health

_LAZY_REEXPORTS = {
    '_deploy_container': '.deploy_container',
    '_deployment_effective_server': '.provider',
    '_do_promote': '.promote',
    '_handle_failure': '.failure',
    '_is_local_deployment_server': '.provider',
    '_mark_deployment_active': '.state',
    '_post_deploy_success': '.state',
    '_regenerate_caddyfile': '.caddy',
    '_resolve_provider_for_service': '.provider',
    'enqueue_smart_deploy_task': '.queue',
    'recover_stalled_queued_deployments': '.queue',
    'should_skip_review_for_commit_message': '.queue',
    'AUTO_APPROVE_COMMIT_MARKERS': '.queue',
    '_is_traefik_not_ready': '.health',
    '_route_misroute_reason': '.health',
    '_is_low_resource_service': '.health',
    '_local_route_timeout_seconds': '.health',
    '_local_container_timeout_seconds': '.health',
    '_wait_for_local_container_healthy': '.health',
    '_wait_for_local_route_ready': '.health',
}

_BUILD_REEXPORTS = {
    'fleet_build_lock', '_detect_exposed_port', '_coerce_int',
    '_is_legacy_default_healthcheck', '_build_platform_healthcheck',
    '_build_runtime_env', '_smart_derive_database_vars', '_smart_derive_redis_vars',
}

_BUILD_DOCKER_REEXPORTS = {'_run_managed_image_post_deploy_hooks', '_docker_safe_segment'}


def __getattr__(name):
    if name in _LAZY_REEXPORTS:
        mod = _importlib.import_module(_LAZY_REEXPORTS[name], __package__)
        return getattr(mod, name)
    if name in _BUILD_DOCKER_REEXPORTS:
        return getattr(_importlib.import_module('.build_docker', __package__), name)
    if name in _BUILD_REEXPORTS:
        return getattr(_importlib.import_module('.build', __package__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
