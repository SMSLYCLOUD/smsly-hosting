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


def _cancel_previous_staged(deployment: Deployment) -> None:
    """Cancel any prior STAGED deployments for the same service.

    Ensures only one staged version is live at a time — the staging URL is
    per-service, so multiple staged containers would collide on the same
    Traefik router and load-balance randomly.
    """
    previous = Deployment.objects.filter(
        service=deployment.service,
        status__in=(Deployment.Status.STAGED, Deployment.Status.HEALTH_CHECK),
    ).exclude(id=deployment.id)

    if not previous.exists():
        return

    client = None
    try:
        client = docker.from_env()
    except Exception:
        pass

    for old in previous:
        append_log(deployment, f"[STAGED] Replacing prior staged deployment {old.id} (commit {old.commit_hash[:8] if old.commit_hash else 'unknown'})\n")
        old.status = Deployment.Status.CANCELLED
        old.finished_at = timezone.now()
        old.build_logs += "\n\n[Cancelled] Superseded by a newer staged deployment."
        old.save(update_fields=['status', 'finished_at', 'build_logs'])
        # Stop old green container
        for c_id in [old.green_container_id, old.container_id]:
            if not c_id or not client:
                continue
            try:
                c = client.containers.get(c_id)
                c.remove(force=True)
                logger.info("Removed old staged container %s for deployment %s", c_id, old.id)
            except docker.errors.NotFound:
                pass
            except Exception as exc:
                logger.warning("Failed to remove old staged container %s: %s", c_id, exc)


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
                    staging_host = ""
                    if staged_only and staging_url:
                        from urllib.parse import urlparse
                        staging_host = urlparse(staging_url).hostname or ""
                    _wait_for_local_route_ready(
                        deployment, service,
                        timeout_seconds=DEPLOY_CONTAINER_TIMEOUT,
                        host_override=staging_host,
                    )

            if staged_only:
                _cancel_previous_staged(deployment)
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

        # The adapter needs STAGING_DOMAIN before it creates the container;
        # Docker labels cannot be added after creation.
        if staged_only:
            deployment.staging_url = service.generate_staging_url()
            deployment.save(update_fields=['staging_url'])

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
        # Build a map of addon_type -> connection_url for this service.
        # The service's OWN addons win, but shared (project-level) addons
        # are the fallback for any type the service doesn't have itself.
        addon_url: dict[str, str] = {}
        for addon in Addon.objects.filter(service=service, status='ACTIVE'):
            if addon.connection_url:
                addon_url.setdefault(addon.addon_type, addon.connection_url)
        # Fill in from shared addons in the same project.
        # SECURITY (addon-theft): only addons NAMED '{type}-shared' are
        # project-wide fallbacks. Ecosystem deploys create shared addons
        # with that exact name. A manually-deployed service's personal
        # addons ('{service}-{type}') must NEVER leak into another
        # service's DATABASE_URL / REDIS_URL — previously this loop
        # matched ANY active addon in the project, so when a manual
        # service shared a project with ecosystem services, every
        # deploy picked up the manual service's private DB URL.
        project = getattr(service, "project", None)
        if project:
            for addon in Addon.objects.filter(
                service__project=project,
                status='ACTIVE',
                name__endswith='-shared',
            ).exclude(service=service):
                if addon.connection_url and addon.addon_type not in addon_url:
                    addon_url[addon.addon_type] = addon.connection_url
        for addon_type, url in addon_url.items():
            env_key = AddonProvisioner.ENV_KEY_MAP.get(addon_type)
            if env_key:
                env_vars[env_key] = url
                if addon_type == 'QDRANT':
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
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

        # For staged deployments, inject the staging domain into the env
        # so the green container's Traefik labels route the staging URL.
        if staged_only and deployment.staging_url:
            from urllib.parse import urlparse
            staging_host = urlparse(deployment.staging_url).hostname or ''
            if staging_host:
                existing_custom = env_vars.get('CUSTOM_DOMAINS', '')
                custom_list = [d.strip() for d in existing_custom.split(',') if d.strip()]
                if staging_host not in custom_list:
                    custom_list.append(staging_host)
                env_vars['CUSTOM_DOMAINS'] = ','.join(custom_list)
                # Also signal the adapter this is a staged deployment
                env_vars['STAGING_DOMAIN'] = staging_host

        # Inject host aliases so Traefik labels include them in Host() rule
        host_aliases = getattr(service, 'host_aliases', None) or []
        if host_aliases:
            alias_hosts = []
            for item in host_aliases:
                if isinstance(item, dict):
                    h = str(item.get('host') or '').strip().lower()
                else:
                    h = str(item or '').strip().lower()
                if h:
                    alias_hosts.append(h)
            if alias_hosts:
                env_vars['HOST_ALIASES'] = ','.join(alias_hosts)

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
                staging_host = ""
                if staged_only and deployment.staging_url:
                    from urllib.parse import urlparse
                    staging_host = urlparse(deployment.staging_url).hostname or ""
                route_ready = _wait_for_local_route_ready(
                    deployment,
                    service,
                    timeout_seconds=route_timeout,
                    host_override=staging_host,
                )
                if not route_ready:
                    # The CONTAINER is healthy (checked above) — only the
                    # edge route lagged. Traefik's Docker provider refreshes
                    # on an interval and can take longer than the timeout
                    # when the daemon is loaded (provider 'context deadline
                    # exceeded' errors for hours on the prod host). Failing
                    # here used to tear down a HEALTHY container, and the
                    # next deploy then re-ran the whole cycle — the very
                    # next deploy of this service succeeded within seconds.
                    # Keep the container: warn, continue, and let the route
                    # register when Traefik catches up.
                    host = (service.public_domain or "").strip() or service.name
                    append_log(
                        deployment,
                        "[ROUTE-CHECK] Route not ready yet, but the container is "
                        f"healthy — keeping deployment alive. Traefik will register "
                        f"the route for {host} when its Docker provider refreshes.\n",
                    )
                    deployment.build_logs = (
                        (deployment.build_logs or "")
                        + f"\n[ROUTE-WARN] Edge route for {host} did not answer within "
                        f"{route_timeout}s; container is healthy and was kept running.\n"
                    )
                    deployment.save(update_fields=["build_logs"])
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

        if staged_only:
            _cancel_previous_staged(deployment)
            deployment.status = Deployment.Status.STAGED
            deployment.staged_at = timezone.now()
            deployment.container_id = resource.resource_id
            deployment.save(update_fields=['status', 'staged_at', 'container_id'])
            broadcast_status(deployment)
            _post_deploy_success(deployment, service)
            append_log(
                deployment,
                f"[STAGED] Deployment staged for review.\n"
                f"Preview URL: {deployment.staging_url}\n"
                f"Container: {resource.resource_id}\n"
            )
            return

        _mark_deployment_active(deployment, "local", "127.0.0.1", resource.resource_id)

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
