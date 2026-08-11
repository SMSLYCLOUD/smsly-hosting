from __future__ import annotations

import logging
import os
from typing import Any

import docker
from django.conf import settings
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.cloud.services.compute import ComputeService
from apps.deployments.constants import CPU_MILLICORES_PER_CORE, DEPLOY_CONTAINER_TIMEOUT
from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    Service,
)
from apps.deployments.models.addons import Addon
from apps.deployments.models.storage import Volume
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    update_stage,
)

from .addons import _ensure_addons_ready, _probe_addon_connectivity
from .build_compose import _build_platform_healthcheck, _build_runtime_env
from .build_docker import _run_managed_image_post_deploy_hooks
from .caddy import _regenerate_caddyfile
from .state import _mark_deployment_active, _post_deploy_success

logger = logging.getLogger(__name__)


def _deploy_container(deployment: Deployment, provider: CloudProvider, image_name: str,
                      staged_only: bool = False) -> None:
    from ..deployment.tasks_deploy import _post_deploy_monitor
    # pylint: disable=too-many-locals, R0914
    update_stage(deployment, 'Deploy', 'running')
    start = timezone.now()

    try:
        service = deployment.service

        # --- Compose mode: containers already running from pipeline ---
        if service.deploy_mode == 'COMPOSE' and image_name.startswith('compose:'):
            from .health import (
                _local_container_timeout_seconds,
                _local_route_timeout_seconds,
                _wait_for_local_container_healthy,
                _wait_for_local_route_ready,
            )
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
                _regenerate_caddyfile()
                if service.is_public:
                    _wait_for_local_route_ready(
                        deployment, service,
                        timeout_seconds=DEPLOY_CONTAINER_TIMEOUT,
                    )

            if staged_only:
                staging_url = service.generate_staging_url(deployment.commit_hash or '')
                deployment.status = Deployment.Status.STAGED
                deployment.staging_url = staging_url
                deployment.staged_at = timezone.now()
                deployment.container_id = container_name
                deployment.save(update_fields=['status', 'staging_url', 'staged_at', 'container_id'])
                broadcast_status(deployment)
                _post_deploy_success(deployment, service)
                append_log(
                    deployment,
                    f"[STAGED] Compose deployment staged for review.\n"
                    f"Preview URL: {staging_url}\n"
                )
                return

            deployment.status = Deployment.Status.ACTIVE
            deployment.finished_at = timezone.now()
            deployment.save(update_fields=['status', 'finished_at'])
            update_stage(
                deployment, 'Deploy', 'success',
                (timezone.now() - start).total_seconds()
            )
            broadcast_status(deployment)

            _post_deploy_success(deployment, service)
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
                except docker.errors.DockerException as _inspect_err:
                    local_cache_error = str(_inspect_err)
            except (docker.errors.DockerException, OSError) as _retag_err:
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

        _ensure_addons_ready(service, deployment)

        from apps.addons.services.addon_provisioner import AddonProvisioner
        for addon in Addon.objects.filter(service=service, status='ACTIVE'):
            env_key = AddonProvisioner.ENV_KEY_MAP.get(addon.addon_type)
            if env_key and addon.connection_url:
                env_vars[env_key] = addon.connection_url
                if addon.addon_type == 'QDRANT':
                    from urllib.parse import urlparse
                    parsed = urlparse(addon.connection_url)
                    env_vars['QDRANT_HOST'] = parsed.hostname or 'localhost'
                    env_vars['QDRANT_PORT'] = str(parsed.port or 6333)

        persist_keys = {
            'ALLOWED_HOSTS', 'DJANGO_ALLOWED_HOSTS', 'MARKETER_ALLOWED_HOSTS',
            'CELERY_BROKER_URL', 'AMQP_URL', 'PUBLIC_DOMAIN', 'API_INTERNAL_URL',
            'SMSLY_BACKEND_URL', 'CUSTOM_DOMAINS',
            'DJANGO_SECRET_KEY', 'FERNET_KEY', 'ADMIN_EMAIL',
        }
        for key in persist_keys:
            val = env_vars.get(key)
            if val:
                obj, created = EnvironmentVariable.objects.get_or_create(
                    service=service, key=key,
                    defaults={'value': val, 'is_secret': key.endswith('_KEY') or key.endswith('_SECRET')},
                )
                if not created and not obj.value:
                    obj.value = val
                    obj.save(update_fields=['value'])

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
            cpu=int(service.cpu_cores * CPU_MILLICORES_PER_CORE),
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
            from .health import (
                _local_container_timeout_seconds,
                _local_route_timeout_seconds,
                _wait_for_local_container_healthy,
                _wait_for_local_route_ready,
            )
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
            # Regenerate Caddyfile AFTER route readiness check to avoid
            # stale Caddyfile pointing to a dead container on failure.
            _regenerate_caddyfile()
            _run_managed_image_post_deploy_hooks(
                deployment,
                service,
                resource.resource_id,
            )

        addon_errors = _probe_addon_connectivity(service, resource.resource_id)
        if addon_errors:
            err_summary = "; ".join(addon_errors)
            append_log(
                deployment,
                "\n🔴 Addon connectivity failed — service container cannot reach "
                "its addon(s):\n"
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

        _mark_deployment_active(deployment, "local", "127.0.0.1", resource.resource_id)

        if staged_only:
            staging_url = service.generate_staging_url(deployment.commit_hash or '')
            deployment.status = Deployment.Status.STAGED
            deployment.staging_url = staging_url
            deployment.staged_at = timezone.now()
            deployment.container_id = resource.resource_id
            deployment.save(update_fields=['status', 'staging_url', 'staged_at', 'container_id'])
            broadcast_status(deployment)
            _post_deploy_success(deployment, service)
            append_log(
                deployment,
                f"[STAGED] Deployment staged for review.\n"
                f"Preview URL: {staging_url}\n"
                f"Container: {resource.resource_id}\n"
            )
            return

        deployment.status = Deployment.Status.ACTIVE
        deployment.container_id = resource.resource_id
        deployment.finished_at = timezone.now()
        deployment.save()

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

        _post_deploy_success(deployment, service)
        append_log(
            deployment,
            "[DEPLOY] ✅ Container started with Traefik routing label applied.\n"
            "Domain accessibility depends on DNS propagation and Traefik config reload.\n"
        )

        _post_deploy_monitor.delay(
            deployment_id=str(deployment.id),
            provider_id=str(provider.id),
            container_id=resource.resource_id,
            image_name=image_name,
        )

    except Exception as e:
        logger.debug("Deploy failed for %s: %s", deployment.id, e)
        update_stage(deployment, 'Deploy', 'failed')
        raise e
