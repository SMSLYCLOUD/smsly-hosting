"""Deployment helper functions — re-export hub for sub-modules."""
from __future__ import annotations

import importlib as _importlib
import logging

logger = logging.getLogger(__name__)

from .env import _env_bool, _env_int  # noqa: F401 — needed by build_compose, health


def _abort_if_cancelled(deployment) -> bool:
    """Refresh the row and stop quietly when the user cancelled mid-flight.

    Cancel is DB-status-only: nothing preempts a worker blocked in a long
    `docker build`, so a zombie can emerge from the build phase holding a
    freshly built image. Without this gate it would DEPLOY cancelled code
    — and, pre fleet-lock fix, it raced the slot thief on the same tag
    ("No such image", 2026-09-22). Call at every build→deploy handoff
    (covers smart + resume paths via _deploy_container and
    _handle_remote_deployment). Returns True when the caller must stop.
    Never raises.
    """
    try:
        from apps.deployments.models import Deployment
        from apps.deployments.utils import append_log, broadcast_status

        try:
            deployment.refresh_from_db()
        except Exception:
            return False
        if deployment.status == Deployment.Status.CANCELLED:
            append_log(
                deployment,
                "\n⏹ Deployment was cancelled during the build — "
                "discarding the built image, nothing deployed.\n",
            )
            try:
                broadcast_status(deployment)
            except Exception:
                pass
            return True
        return False
    except Exception:
        return False

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
    '_probe_addon_connectivity': '.addons',
    '_ensure_addons_ready': '.addons',
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
