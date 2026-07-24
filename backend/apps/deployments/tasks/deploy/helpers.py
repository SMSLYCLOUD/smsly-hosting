"""Deployment helper functions — env assembly, container deployment, health checks, failure handling."""
"""Tasks module."""
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
from contextlib import suppress
from urllib.parse import unquote, urlparse

import docker
import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.cloud.services.compute import ComputeService
from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    PlatformConfig,
    Service,
)
from apps.deployments.models.addons import Addon
from apps.deployments.models.storage import Volume
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    update_stage,
)

# Imports for AIProviderSettings; jules_fix is imported lazily inside tasks
# Note: AIProviderSettings is not available in agent mode
try:
    from apps.intelligence.models import AIProviderSettings as _AIProviderSettings
except (ImportError, RuntimeError):
    _AIProviderSettings = None  # type: ignore[assignment]
AIProviderSettings = _AIProviderSettings

logger = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, str(default))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _ensure_addons_ready(service, deployment) -> None:
    """Block deployment until all active addons are healthy and reachable.

    Raises RuntimeError if any addon is not ACTIVE or fails its health check.
    This prevents service containers from starting before their dependencies.
    """
    from apps.addons.services.addon_provisioner import addon_provisioner
    from apps.deployments.models.addons import Addon

    addons = Addon.objects.filter(service=service, status='ACTIVE')
    for addon in addons:
        if not addon.connection_url:
            raise RuntimeError(
                f"Addon {addon.addon_type} ({addon.name}) is ACTIVE but has no "
                f"connection URL. Provisioning may have failed silently."
            )
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        cid, running = addon_provisioner._container_status(container_name)
        if not cid or not running:
            raise RuntimeError(
                f"Addon {addon.addon_type} ({addon.name}) container "
                f"{container_name} is not running (cid={cid}, running={running}). "
                f"The service cannot start without its addon."
            )
        # Verify network alias is resolvable from the Docker network
        probe_id = secrets.token_hex(4)
        try:
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(addon.connection_url)
            hostname = parsed.hostname or container_name
            inspect_cmd = [
                'docker', 'inspect', '-f',
                '{{range .NetworkSettings.Networks}}{{range .Aliases}}{{.}} {{end}}{{end}}',
                container_name,
            ]
            logger.debug("[probe:%s] Running: %s", probe_id, shlex.join(inspect_cmd))
            result = subprocess.run(
                inspect_cmd,
                capture_output=True, text=True, timeout=5,
            )
            aliases = (result.stdout or '').split()
            if hostname not in aliases:
                # Try to repair the alias
                repair_cmd = [
                    'docker', 'network', 'connect', '--alias', hostname,
                    addon_provisioner.network_name, container_name,
                ]
                logger.debug("[probe:%s] Running: %s", probe_id, shlex.join(repair_cmd))
                subprocess.run(
                    repair_cmd,
                    capture_output=True, check=False, timeout=5,
                )
                logger.warning(
                    "[probe:%s] Repaired missing network alias %s for addon %s",
                    probe_id, hostname, container_name,
                )
        except Exception as exc:
            logger.warning(
                "[probe:%s] Addon network alias check failed for %s: %s",
                probe_id, container_name, exc,
            )


def _probe_addon_connectivity(service, container_id: str) -> list[str]:
    """Probe addon connectivity from inside the service container.

    Returns a list of error messages for addons that are unreachable.
    Empty list means all addons are reachable.
    """
    from apps.deployments.models.addons import Addon
    from urllib.parse import urlparse as _urlparse

    errors = []
    addons = Addon.objects.filter(service=service, status='ACTIVE')
    if not addons.exists():
        return errors

    try:
        client = docker.from_env()
    except Exception:
        return errors

    for addon in addons:
        if not addon.connection_url:
            continue

        parsed = _urlparse(addon.connection_url)
        hostname = unquote(parsed.hostname or '')
        port = parsed.port
        if not hostname or not port:
            continue

        # Try to resolve and connect from inside the service container
        try:
            # Use docker exec to test connectivity from within the container
            # Try TCP socket first (works for Postgres, Redis, MySQL, etc.)
            test_cmd = (
                f"python3 -c \""
                f"import socket; s=socket.socket(); s.settimeout(5); "
                f"s.connect(('{hostname}', {port})); s.close(); print('OK')"
                f"\" 2>/dev/null || "
                f"python -c \""
                f"import socket; s=socket.socket(); s.settimeout(5); "
                f"s.connect(('{hostname}', {port})); s.close(); print('OK')"
                f"\" 2>/dev/null || "
                f"bash -c 'echo > /dev/tcp/{hostname}/{port}' 2>/dev/null && echo OK"
            )
            result = client.containers.get(container_id).exec_run(
                ["bash", "-c", test_cmd],
                timeout=10,
            )
            output = (result.output or b"").decode("utf-8", errors="replace").strip()
            if result.exit_code != 0 or "OK" not in output:
                # TCP check failed — try HTTP health check as fallback
                # (some addons expose /health or / ready endpoints)
                try:
                    http_url = f"http://{hostname}:{port}/"
                    resp = requests.get(http_url, timeout=5, verify=False)
                    if resp.status_code < 500:
                        continue  # addon is reachable via HTTP, TCP check may be wrong
                except requests.RequestException:
                    pass  # HTTP also failed, report the original TCP error
                errors.append(
                    f"Addon {addon.addon_type} ({addon.name}): "
                    f"service container cannot reach {hostname}:{port} "
                    f"(exit={result.exit_code}, output={output[:200]})"
                )
        except Exception as exc:
            errors.append(
                f"Addon {addon.addon_type} ({addon.name}): "
                f"connectivity probe failed: {exc}"
            )

    return errors


AUTO_APPROVE_COMMIT_MARKERS = (
    "auto-redeploy",
    "auto-remediation",
    "auto-rollback",
    "auto-restart",
    "[auto-fix]",
    "service restart",
)


def should_skip_review_for_commit_message(message: str) -> bool:
    """Return True for system-created deployments that must not pause at REVIEW."""
    normalized = str(message or "").strip().lower()
    return any(marker in normalized for marker in AUTO_APPROVE_COMMIT_MARKERS)


def _current_agent_node_queue() -> str:
    """Return this lite agent's dedicated deploy queue, if running as an agent."""
    if str(os.environ.get("MODE", "")).strip().lower() != "agent":
        return ""
    queue = str(os.environ.get("SMSLY_NODE_QUEUE", "")).strip()
    if not queue or queue == "deploy":
        logger.warning(
            "Agent mode is running without a dedicated SMSLY_NODE_QUEUE; "
            "falling back to the shared deploy queue."
        )
        return ""
    return queue

def enqueue_smart_deploy_task(
    deployment_id: str,
    provider_id: str,
    skip_review: bool = False,
):
    from ..deployment.tasks_deploy import smart_deploy_task
    """
    Enqueue a deployment, using a dedicated node queue on lite agents.

    Full installs and lite agents both use a broker local to the server that
    receives the API request. Lite agents still route API-triggered deploys to
    their per-node queue so only that node's worker consumes them.
    """
    kwargs = {
        "deployment_id": str(deployment_id),
        "provider_id": str(provider_id),
        "skip_review": skip_review,
    }
    queue = _current_agent_node_queue()
    if queue:
        return smart_deploy_task.apply_async(
            kwargs=kwargs,
            queue=queue,
            routing_key=queue,
        )
    return smart_deploy_task.delay(**kwargs)

def _resolve_provider_for_service(service: Service, prefer_local: bool = False):
    """
    Strict one-to-one provider resolution. No silent fallbacks.
    - If service has a provider, it MUST be active and we return it.
    - If no provider but prefer_local, return LOCAL if active.
    - Fail explicitly if intended target unavailable.
    """
    if service.provider:
        if service.provider.is_active:
            return service.provider
        return None # Explicitly fail

    if prefer_local:
        local = CloudProvider.objects.filter(
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True
        ).first()
        if local:
            return local
        return None

    # Implicit default: if no explicit target, try to find one but don't fallback silently later.
    # We will pick a default global remote or local, but once picked, it's fixed.
    remote = CloudProvider.objects.filter(
        provider_type=CloudProvider.ProviderType.REMOTE,
        is_active=True
    ).first()
    if remote:
        return remote

    return CloudProvider.objects.filter(
        provider_type=CloudProvider.ProviderType.LOCAL,
        is_active=True
    ).first()

def _deployment_effective_server(deployment):
    """Return the server this deployment should use, honoring explicit local."""
    if bool(getattr(deployment, "target_is_local", False)):
        return None

    server = getattr(deployment, "target_server", None) or getattr(deployment.service, "server", None)
    if server:
        return server

    # Fallback: if the service has active runtime metadata pointing to a remote
    # node (e.g. after a prior successful remote deploy), resolve the
    # ManagedServer from the verified host IP so redeploy stays on that node.
    service = deployment.service
    active_type = getattr(service, "active_target_type", None) or ""
    if active_type.lower() in ("remote", "lite_agent"):
        host_ip = getattr(service, "active_host_ip", None)
        if host_ip:
            from apps.deployments.models.core import ManagedServer
            srv = ManagedServer.objects.filter(host=host_ip).first()
            if srv:
                return srv
            srv = ManagedServer.objects.filter(private_ip=host_ip).first()
            if srv:
                return srv
            srv = ManagedServer.objects.filter(wg_address=host_ip).first()
            if srv:
                return srv

    return None

def _is_local_deployment_server(server, config) -> bool:
    if server is None:
        return True
    return (
        bool(getattr(server, "is_primary", False))
        or str(getattr(server, "host", "") or "") == str(getattr(config, "server_ip", "") or "")
    )

def recover_stalled_queued_deployments(limit: int = 100) -> dict:
    """
    Re-publish queued deployment tasks after a platform restart/update.

    Automated deployments keep their auto-approval semantics even when the
    original Celery publish was lost during an update.
    """
    from celery.result import AsyncResult

    results = {"seen": 0, "queued": 0, "skipped": 0, "failed": 0}
    deployments = (
        Deployment.objects.filter(status=Deployment.Status.QUEUED)
        .select_related("service", "service__provider")
        .order_by("created_at")[:limit]
    )
    for deployment in deployments:
        results["seen"] += 1
        try:
            task_state = AsyncResult(str(deployment.id)).state
        except Exception:
            task_state = None
        if task_state in ("STARTED", "RECEIVED", "RETRY"):
            logger.info(
                "Skipping re-queue for %s: task is in state %s",
                deployment.id,
                task_state,
            )
            results["skipped"] += 1
            continue
        provider = cache.get(f"provider_resolve:{deployment.service_id}")
        if provider is None:
            provider = _resolve_provider_for_service(
                deployment.service,
                prefer_local=bool(getattr(deployment, "target_is_local", False)),
            )
            if provider:
                cache.set(f"provider_resolve:{deployment.service_id}", provider, timeout=60)
        if not provider:
            append_log(
                deployment,
                "\n[queue-restore] No active provider available; leaving deployment queued.\n",
            )
            results["skipped"] += 1
            continue

        skip_review = deployment.is_rollback or should_skip_review_for_commit_message(
            deployment.commit_message
        )
        try:
            enqueue_smart_deploy_task(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
                skip_review=skip_review,
            )
            append_log(
                deployment,
                f"\n[queue-restore] Requeued deployment task (skip_review={skip_review}).\n",
            )
            results["queued"] += 1
        except Exception as exc:  # pragma: no cover - broker/runtime failure
            logger.exception(
                "Failed to restore queued deployment task for deployment=%s",
                deployment.id,
            )
            append_log(
                deployment,
                f"\n[queue-restore] Failed to requeue deployment task: {exc}\n",
            )
            results["failed"] += 1
    return results

def _regenerate_caddyfile():
    """Regenerate and apply the Caddyfile with current service domains.

    Called after successful deployments so new services get Caddy site blocks
    (and therefore SSL certificates) without requiring a manual Settings save.
    """
    try:
        config = PlatformConfig.load()
        from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile
        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            logger.info("Caddyfile regenerated after deployment")
        else:
            logger.warning("Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not regenerate Caddyfile: %s", exc)


# ── Re-exports from deploy_build ────────────────────────────
import importlib as _importlib

_BUILD_REEXPORTS = {
    'fleet_build_lock', '_run_managed_image_post_deploy_hooks',
    '_docker_safe_segment', '_detect_exposed_port', '_coerce_int',
    '_is_legacy_default_healthcheck', '_build_platform_healthcheck',
    '_build_runtime_env', '_smart_derive_database_vars', '_smart_derive_redis_vars',
}


def __getattr__(name):
    if name in _BUILD_REEXPORTS:
        return getattr(_importlib.import_module('.build', __package__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Re-exports from deploy_health ───────────────────────────
from .health import (  # noqa: F401
    _is_traefik_not_ready,
    _route_misroute_reason,
    _is_low_resource_service,
    _local_route_timeout_seconds,
    _local_container_timeout_seconds,
    _wait_for_local_container_healthy,
    _wait_for_local_route_ready,
)
def _deploy_container(deployment, provider, image_name):
    """Deploy the built image to the cloud provider."""
    from ...tasks_deploy import _post_deploy_monitor
    # pylint: disable=too-many-locals, R0914
    update_stage(deployment, 'Deploy', 'running')
    start = timezone.now()

    try:
        service = deployment.service

        # --- Compose mode: containers already running from pipeline ---
        if service.deploy_mode == 'COMPOSE' and image_name.startswith('compose:'):
            container_name = image_name.split(':', 1)[1]
            deployment.status = Deployment.Status.HEALTH_CHECK
            deployment.container_id = container_name
            deployment.save(update_fields=['status', 'container_id'])
            broadcast_status(deployment)

            if provider.provider_type == CloudProvider.ProviderType.LOCAL:
                route_timeout = _local_route_timeout_seconds(service)
                container_timeout = _local_container_timeout_seconds(service)
                container_ready = _wait_for_local_container_healthy(
                    deployment, container_name, timeout_seconds=container_timeout,
                )
                if not container_ready:
                    raise RuntimeError(
                        f"Container failed readiness checks: {container_name}"
                    )
                # Route check AFTER container is healthy — poll until active
                _regenerate_caddyfile()
                if service.is_public:
                    _wait_for_local_route_ready(
                        deployment, service,
                        timeout_seconds=300,  # 5 minute cap
                    )

            deployment.status = Deployment.Status.ACTIVE
            deployment.finished_at = timezone.now()
            deployment.save(update_fields=['status', 'finished_at'])
            update_stage(
                deployment, 'Deploy', 'success',
                (timezone.now() - start).total_seconds()
            )
            broadcast_status(deployment)

            # Post success commit status to GitHub (non-blocking)
            with suppress(Exception):
                from .tasks_commit_status import update_commit_status
                update_commit_status.delay(
                    str(deployment.id), 'success', 'Deployment active'
                )

            _regenerate_caddyfile()
            append_log(
                deployment,
                f"[OK] Compose deployment successful. "
                f"Container: {container_name}\n"
            )

            _post_deploy_monitor.delay(
                deployment_id=str(deployment.id), provider_id=str(provider.id),
                container_id=container_name, image_name=image_name,
            )
            return

        # --- Standard single-container deploy ---
        compute = ComputeService(provider)

        # Explicitly pull image before deployment to avoid 404/Not Found
        append_log(deployment, f"Pulling image {image_name}...\n")
        if not compute.pull_image(image_name):
            append_log(deployment, f"Warning: Registry pull failed for {image_name}. "
                                   "Attempting deployment using local cache...\n")
            # When pull_image fails for a registry-prefixed image (e.g.
            # registry:5000/smsly/myapp:abc123), Docker may not find it
            # locally even though the build phase tagged it.  Try to
            # retag the original local image as a fallback.
            image_available_after_pull_failure = False
            local_cache_error = ""
            try:
                _client = docker.from_env()
                try:
                    _client.images.get(image_name)
                    image_available_after_pull_failure = True
                except docker.errors.ImageNotFound:
                    registry_prefix = getattr(settings, 'CONTAINER_REGISTRY_URL', None)
                    if registry_prefix and image_name.startswith(registry_prefix):
                        local_tag = image_name[len(registry_prefix) + 1:]
                        try:
                            local_img = _client.images.get(local_tag)
                            local_img.tag(image_name)
                            image_available_after_pull_failure = True
                            append_log(
                                deployment,
                                f"Retagged local {local_tag} -> {image_name}\n",
                            )
                        except docker.errors.ImageNotFound:
                            # Try the original name parts without registry
                            fallback = "/".join(local_tag.split("/")[1:]) if "/" in local_tag else ""
                            if fallback:
                                try:
                                    local_img = _client.images.get(fallback)
                                    local_img.tag(image_name)
                                    image_available_after_pull_failure = True
                                    append_log(
                                        deployment,
                                        f"Retagged local {fallback} -> {image_name}\n",
                                    )
                                except docker.errors.ImageNotFound:
                                    append_log(
                                        deployment,
                                        "Local cache unavailable.\n",
                                    )
                            else:
                                append_log(
                                    deployment,
                                    "Local cache unavailable.\n",
                                )
                    else:
                        append_log(
                            deployment,
                            "Local cache unavailable.\n",
                        )
                except Exception as _inspect_err:
                    local_cache_error = str(_inspect_err)
            except Exception as _retag_err:
                local_cache_error = str(_retag_err)
                logger.warning("Image retag fallback failed: %s", _retag_err)
            if not image_available_after_pull_failure:
                detail = (
                    f" Local cache check failed: {local_cache_error}"
                    if local_cache_error
                    else ""
                )
                raise RuntimeError(
                    "Image pull failed and the image is not present in the "
                    f"target node's Docker cache: {image_name}. For lite-agent "
                    "deployments, verify the master registry is reachable from "
                    "the node and listed in Docker insecure-registries."
                    f"{detail}"
                )

        env_vars = _build_runtime_env(service, image_name=image_name)

        # Verify all addons are healthy before injecting their URLs.
        # This prevents deploying a service container when its database/cache
        # addon is not running or has no connection URL.
        _ensure_addons_ready(service, deployment)

        # Inject addon connection URLs into deployed container.
        # Always overwrite: addon URLs may change on re-provision (new password,
        # new container). Using setdefault would keep stale URLs from prior deploys.
        from apps.addons.services.addon_provisioner import AddonProvisioner
        for addon in Addon.objects.filter(service=service, status='ACTIVE'):
            env_key = AddonProvisioner.ENV_KEY_MAP.get(addon.addon_type)
            if env_key and addon.connection_url:
                env_vars[env_key] = addon.connection_url
                # Qdrant: also set host/port for apps that expect QDRANT_HOST
                if addon.addon_type == 'QDRANT':
                    parsed = urlparse(addon.connection_url)
                    env_vars['QDRANT_HOST'] = parsed.hostname or 'localhost'
                    env_vars['QDRANT_PORT'] = str(parsed.port or 6333)

        # Persist resolved env vars to DB — only fills vars that are empty in DB
        persist_keys = {
            'ALLOWED_HOSTS', 'DJANGO_ALLOWED_HOSTS', 'MARKETER_ALLOWED_HOSTS',
            'CELERY_BROKER_URL', 'AMQP_URL', 'PUBLIC_DOMAIN', 'API_INTERNAL_URL',
            'SMSLY_BACKEND_URL', 'CUSTOM_DOMAINS',
            'DJANGO_SECRET_KEY', 'FERNET_KEY', 'ADMIN_EMAIL',
        }
        for key in persist_keys:
            val = env_vars.get(key)
            if val:
                _, created = EnvironmentVariable.objects.get_or_create(
                    service=service, key=key,
                    defaults={'value': val, 'is_secret': key.endswith('_KEY') or key.endswith('_SECRET')},
                )
                if not created:
                    existing = EnvironmentVariable.objects.filter(service=service, key=key).first()
                    if existing and not existing.value:
                        existing.value = val
                        existing.save(update_fields=['value'])

        volumes = [{'name': v.name, 'mount_path': v.mount_path}
                   for v in Volume.objects.filter(service=service)]

        healthcheck = _build_platform_healthcheck(service, env_vars)
        if not healthcheck:
            append_log(
                deployment,
                "[HEALTH-CHECK] Using image/native health checks (or running-state readiness).\n",
            )

        resource = compute.deploy_container(
            name=service.name,
            image=image_name,
            env_vars=env_vars,
            cpu=int(service.cpu_cores * 1024),
            memory=service.memory_mb,
            replicas=getattr(deployment, 'queued_min_replicas', None) or service.min_replicas,
            volumes=volumes,
            healthcheck=healthcheck,
            restart_policy=service.restart_policy,
            command=(service.start_command or None),
            vpa_enabled=service.vpa_enabled,
            service_id=str(service.id),
        )

        deployment.status = Deployment.Status.HEALTH_CHECK
        deployment.green_container_id = resource.resource_id
        deployment.save(update_fields=['status', 'green_container_id'])
        broadcast_status(deployment)

        if provider.provider_type == CloudProvider.ProviderType.LOCAL:
            container_timeout = _local_container_timeout_seconds(service)
            container_ready = _wait_for_local_container_healthy(
                deployment,
                resource.resource_id,
                timeout_seconds=container_timeout,
            )
            if not container_ready:
                raise RuntimeError(
                    f"Container failed readiness checks for service {service.name}"
                )
            # Route check after container is healthy (standard deploy)
            _regenerate_caddyfile()
            if service.is_public:
                route_timeout = _local_route_timeout_seconds(service)
                route_ready = _wait_for_local_route_ready(
                    deployment, service, timeout_seconds=route_timeout,
                )
                if not route_ready:
                    host = (service.public_domain or "").strip() or service.name
                    raise RuntimeError(
                        f"Route for {host} did not become ready after deployment. "
                        "Caddy/Traefik may still be returning 404 for this host."
                    )
            _run_managed_image_post_deploy_hooks(
                deployment,
                service,
                resource.resource_id,
            )

        # ── ADDON CONNECTIVITY PROBE ──
        # Verify the service container can actually reach its addons
        # (Postgres, Redis, etc.) before marking the deploy as ACTIVE.
        addon_errors = _probe_addon_connectivity(service, resource.resource_id)
        if addon_errors:
            err_summary = "; ".join(addon_errors)
            append_log(
                deployment,
                f"\n🔴 Addon connectivity failed — service container cannot reach "
                f"its addon(s):\n"
                + "\n".join(f"  - {e}" for e in addon_errors)
                + "\n\nThe container will be marked FAILED. Common causes:\n"
                "  1. Addon container is on a different Docker network\n"
                "  2. Network alias was not attached to the addon container\n"
                "  3. Docker DNS resolution failed between containers\n"
                "  4. Addon is not yet accepting connections\n"
            )
            deployment.status = 'FAILED'
            deployment.finished_at = timezone.now()
            deployment.build_logs += f"\n[ADDON-CONNECTIVITY] {err_summary}\n"
            deployment.save()
            broadcast_status(deployment)
            raise RuntimeError(
                f"Addon connectivity check failed: {err_summary}"
            )
        else:
            append_log(
                deployment,
                "[ADDON-CONNECTIVITY] All addon connections verified from service container.\n",
            )

        # Container is live with Traefik labels - mark ACTIVE.
        # Local adapter may internally perform staged blue-green promotion
        # before returning the final live container ID.

        # ── MISSION RULE 3: POST-DEPLOYMENT VERIFICATION (LOCAL) ──
        # Since this is local, if the adapter succeeded, we just explicitly save
        # the verified target metadata to the database.
        deployment.verified_target_type = "local"
        deployment.verified_host_ip = "127.0.0.1"
        deployment.verified_runtime_id = resource.resource_id
        deployment.verified_at = timezone.now()

        deployment.status = Deployment.Status.ACTIVE
        deployment.container_id = resource.resource_id
        deployment.finished_at = timezone.now()
        deployment.save()  # full save() triggers model hook that cancels other ACTIVE deploys

        service.active_target_type = "local"
        service.active_host_ip = "127.0.0.1"
        service.active_runtime_id = resource.resource_id
        service.save(update_fields=['active_target_type', 'active_host_ip', 'active_runtime_id'])


        update_stage(
            deployment,
            'Deploy',
            'done',
            (timezone.now() - start).total_seconds()
        )
        broadcast_status(deployment)

        # Post success commit status to GitHub (non-blocking)
        with suppress(Exception):
            from .tasks_commit_status import update_commit_status
            update_commit_status.delay(
                str(deployment.id), 'success', 'Deployment active'
            )

        # Regenerate local Caddyfile routing so new service domains resolve
        if provider.provider_type == CloudProvider.ProviderType.LOCAL:
            _regenerate_caddyfile()
        append_log(
            deployment,
            "[DEPLOY] ✅ Container started with Traefik routing label applied.\n"
            "Domain accessibility depends on DNS propagation and Traefik config reload.\n"
        )

        # Post-deploy runtime monitor (watches for crashes)
        _post_deploy_monitor.delay(
            deployment_id=str(deployment.id),
            provider_id=str(provider.id),
            container_id=resource.resource_id,
            image_name=image_name,
        )

    except Exception as e:
        update_stage(deployment, 'Deploy', 'failed')
        raise e

def _do_promote(deployment, provider):
    """
    Shared promotion logic for both auto and manual promote.

    1. Verify green container is still healthy
    2. Call adapter.promote_container() to swap old ← green
    3. Mark deployment ACTIVE
    4. Regenerate Caddyfile routing
    """
    service = deployment.service
    green_id = deployment.green_container_id
    if not green_id:
        raise RuntimeError("No green container ID on deployment — cannot promote")

    compute = ComputeService(provider)
    adapter = compute.adapter

    # Only LocalAdapter supports promote_container
    if not hasattr(adapter, 'promote_container'):
        # Non-local providers: just mark ACTIVE (they handle routing differently)
        # ── MISSION RULE 3: POST-DEPLOYMENT VERIFICATION ──
        # Since this is non-local promote, we'll mark the verified fields based on the intended remote type.
        # But wait, actually, remote deployments don't go through `_do_promote` locally. They go through `_poll_remote_deployment`.
        # Just in case, we will fill in the generic metadata.
        target_type = "remote" if provider.provider_type == CloudProvider.ProviderType.REMOTE else "lite_agent"
        host_ip = "unknown"
        if getattr(provider, 'server', None):
            host_ip = provider.server.private_ip or provider.server.host

        deployment.verified_target_type = target_type
        deployment.verified_host_ip = host_ip
        deployment.verified_runtime_id = green_id
        deployment.verified_at = timezone.now()

        deployment.container_id = green_id
        deployment.status = Deployment.Status.ACTIVE
        deployment.finished_at = timezone.now()
        deployment.save()

        service.active_target_type = target_type
        service.active_host_ip = host_ip
        service.active_runtime_id = green_id
        service.save(update_fields=['active_target_type', 'active_host_ip', 'active_runtime_id'])

        broadcast_status(deployment)

        # Post success commit status to GitHub (non-blocking)
        with suppress(Exception):
            from .tasks_commit_status import update_commit_status
            update_commit_status.delay(
                str(deployment.id), 'success', 'Deployment active'
            )

        _regenerate_caddyfile()
        return

    # Perform atomic cutover
    promoted_id = adapter.promote_container(service.name, green_id)

    # ── MISSION RULE 3: POST-DEPLOYMENT VERIFICATION ──
    deployment.verified_target_type = "local"
    deployment.verified_host_ip = "127.0.0.1"
    deployment.verified_runtime_id = promoted_id
    deployment.verified_at = timezone.now()

    deployment.container_id = promoted_id
    deployment.status = Deployment.Status.ACTIVE
    deployment.finished_at = timezone.now()
    deployment.save()

    service.active_target_type = "local"
    service.active_host_ip = "127.0.0.1"
    service.active_runtime_id = promoted_id
    service.save(update_fields=['active_target_type', 'active_host_ip', 'active_runtime_id'])

    broadcast_status(deployment)

    # Post success commit status to GitHub (non-blocking)
    with suppress(Exception):
        from .tasks_commit_status import update_commit_status
        update_commit_status.delay(
            str(deployment.id), 'success', 'Deployment active'
        )

    _regenerate_caddyfile()
    append_log(
        deployment,
        f"[OK] Deployment promoted to ACTIVE. Container: {promoted_id}\n"
    )


    # Route readiness check after promotion
    if provider.provider_type == CloudProvider.ProviderType.LOCAL:
        route_timeout = _local_route_timeout_seconds(service)
        _wait_for_local_route_ready(
            deployment, service, timeout_seconds=route_timeout,
        )


def _escalate_to_ai(deployment, service, container_logs):
    """
    Escalate an unknown runtime error to AI models with full code context.
    Uses all configured AI providers via ask_with_fallback.
    """
    try:
        from apps.intelligence.providers import ask_with_fallback

        # Build rich context: logs + service info + env vars (masked)
        env_summary = ", ".join(
            f"{ev.key}={'***' if ev.is_secret else ev.value}"
            for ev in service.env_vars.all()
        )

        prompt = (
            f"A deployed container for service '{service.name}' crashed immediately "
            f"after deployment. Analyze the logs and provide:\n"
            f"1. Root cause of the crash\n"
            f"2. Specific fix (env var to add, config to change, code to fix)\n"
            f"3. Whether this can be auto-fixed by the platform\n\n"
            f"Service: {service.name}\n"
            f"Deploy type: {service.deploy_type}\n"
            f"Image: {service.docker_image or 'built from git'}\n"
            f"Git repo: {service.repository_url}\n"
            f"Env vars: {env_summary}\n\n"
            f"--- CONTAINER LOGS (last 200 lines) ---\n"
            f"{container_logs[-4000:]}\n"
            f"--- END LOGS ---\n\n"
            f"Return a JSON object:\n"
            f'{{\n'
            f'  "root_cause": "Brief description",\n'
            f'  "fix": "Specific actionable fix",\n'
            f'  "env_vars_needed": {{"KEY": "value_or_empty"}},\n'
            f'  "auto_fixable": true/false,\n'
            f'  "severity": "critical/warning/info"\n'
            f'}}\n'
        )

        response, provider_name = ask_with_fallback(prompt)
        deployment.ai_diagnosis = response
        deployment.save(update_fields=['ai_diagnosis'])

        append_log(
            deployment,
            f"\n🤖 AI Diagnosis ({provider_name}):\n{response[:2000]}\n"
        )

        # Try to parse and auto-apply AI suggestions
        from apps.deployments.utils import parse_ai_resource_recommendation
        parsed = parse_ai_resource_recommendation(response)
        if parsed and parsed.get('env_vars_needed'):
            from apps.deployments.services.error_resolver import _apply_fix
            fix = {'env': parsed['env_vars_needed']}
            action = _apply_fix(fix, re.match('', ''), '', service, deployment)
            if action:
                append_log(deployment, f"  ✅ AI-suggested fix applied: {action}\n")

    except Exception as e:
        logger.warning("AI escalation failed for deployment %s: %s",
                       deployment.id, e)
        append_log(deployment, f"\n🤖 AI diagnosis unavailable: {e}\n")

def _handle_failure(_task, deployment, error_msg, reason):
    """Centralized failure handling with pattern resolver + AI escalation."""
    logger.error("%s: %s", reason, error_msg)

    if deployment:
        deployment.refresh_from_db()
        if deployment.status != 'CANCELLED':
            deployment.status = 'FAILED'
            deployment.finished_at = timezone.now()

            # Post failure commit status to GitHub (non-blocking)
            with suppress(Exception):
                from .tasks_commit_status import update_commit_status
                update_commit_status.delay(
                    str(deployment.id), 'failure', f'{reason}: {error_msg}'[:140]
                )

            # Sanitize inputs for PostgreSQL
            safe_reason = str(reason).replace('\x00', '')
            safe_msg = str(error_msg).replace('\x00', '')

            deployment.build_logs += f"\n✗ {safe_reason}: {safe_msg}\n"
            deployment.save()
            broadcast_status(deployment)

            # Cleanup orphaned container if one was created
            try:
                if deployment.green_container_id or deployment.container_id:
                    import docker
                    client = docker.from_env()
                    c_ids_to_remove = [id for id in [deployment.green_container_id, deployment.container_id] if id]
                    cleaned_any = False
                    for c_id in set(c_ids_to_remove):
                        try:
                            container = client.containers.get(c_id)
                            container.remove(force=True)
                            logger.info(f"Cleaned up orphaned container {c_id} for failed deployment {deployment.id}")
                            cleaned_any = True
                        except docker.errors.NotFound:
                            pass
                        except Exception as e:
                            logger.warning(f"Failed to cleanup container {c_id}: {e}")
                    if cleaned_any:
                        deployment.build_logs += "\n🧹 Cleaned up orphaned container resources.\n"
                        deployment.save(update_fields=['build_logs'])
                # Cleanup build artifacts to free disk space
                from apps.deployments.services.pipeline import _get_builds_root
                build_dir = os.path.join(
                    _get_builds_root(),
                    f"svc_{deployment.service_id}",
                )
                if os.path.isdir(build_dir):
                    shutil.rmtree(build_dir, ignore_errors=True)
                    logger.info("Cleaned up build directory %s for failed deployment %s", build_dir, deployment.id)
            except Exception as e:
                logger.warning(f"Docker client error during failure cleanup: {e}")

            try:
                from apps.core.tasks.alerts import alert_user_task
                alert_user_task.delay(deployment_id=str(deployment.id), error_message=f"{reason}: {error_msg}")
            except Exception as alert_err:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to queue deployment failure alert: %s", alert_err)

            # Step 1: Pattern resolver on build logs (instant)
            try:
                from apps.deployments.services.error_resolver import (
                    diagnose_runtime_logs,
                )
                diagnose_runtime_logs(
                    deployment.build_logs,
                    service=deployment.service,
                    deployment=deployment,
                    auto_apply=True,
                )
            except Exception as e:
                logger.warning("Pattern resolver failed: %s", e)

            # Step 2: AI diagnosis (async)
            try:
                from apps.deployments.tasks.ai.tasks_ai import analyze_failure_task
                analyze_failure_task.delay(deployment_id=str(deployment.id))
            except ImportError:
                pass  # Ignore if module cannot be imported
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.warning("Failed to trigger AI failure task: %s", e)

            # Step 3: Jules auto-fix (async) — tries to fix and redeploy
            try:
                from apps.intelligence.jules_fix import jules_fix_deployment_failure
                service = deployment.service
                # Only trigger if Jules has an API key configured
                if not AIProviderSettings:
                    logger.debug("Jules auto-fix skipped: intelligence app not available in agent mode")
                elif not AIProviderSettings.get_solo().jules_api_key:
                    logger.debug("Jules auto-fix skipped: no Jules API key configured")
                elif not service.repository_url:
                    logger.debug("Jules auto-fix skipped: service has no repository_url")
                else:
                    # Derive repo_path from standard build location
                    from apps.deployments.services.pipeline import _get_builds_root
                    _builds_root = _get_builds_root()
                    repo_path = os.path.join(_builds_root, f"svc_{service.id}")
                    if not os.path.isdir(repo_path):
                        repo_path = ""

                    # Use the full repository URL for git operations
                    jules_fix_deployment_failure.delay(
                        deployment_id=str(deployment.id),
                        logs=deployment.build_logs or error_msg,
                        repo_path=repo_path,
                        repo_url=service.repository_url,
                    )
                    logger.info(
                        "Jules auto-fix triggered for deployment %s (repo=%s)",
                        deployment.id, service.repository_url,
                    )
            except ImportError:
                logger.debug("Jules auto-fix skipped: jules_fix module not available")
            except Exception as e:
                logger.warning("Failed to trigger Jules auto-fix: %s", e)

            # Step 4: Self-healing for remote deployment failures
            try:
                target_server = getattr(deployment, "target_server", None) or getattr(deployment.service, "server", None)
                if target_server and (target_server.ssh_key or target_server.ssh_password):
                    logger.info(
                        "Triggering self-healing for remote deployment %s on server %s",
                        deployment.id, target_server.name,
                    )
                    from .tasks_deploy_remote import self_heal_remote_deployment
                    self_heal_remote_deployment.delay(
                        deployment_id=str(deployment.id),
                        server_id=str(target_server.id),
                    )
            except Exception as e:
                logger.warning("Failed to trigger self-healing: %s", e)

    # Never auto-retry failed deployments.
    # Build failures are deterministic and system failures should be
    # investigated, not blindly retried. Users can manually redeploy.
    logger.error("Deployment failed (%s), not retrying: %s", reason, error_msg)


