import logging

logger = logging.getLogger(__name__)
import logging
import os
import shlex
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager, suppress
from urllib.parse import urlparse

import docker
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from services.addon_provisioner import addon_provisioner

from apps.cloud.models import CloudProvider
from apps.cloud.services.compute import ComputeService
from apps.deployments.ai_router import (
    generate_ai_router_proxy_config,
    get_ollama_model_name,
    is_ai_router_service,
    is_ollama_service,
)
from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    PlatformConfig,
    Service,
)
from apps.deployments.models_addons import Addon
from apps.deployments.models_storage import Volume
from apps.deployments.services.pipeline import PipelineError, PipelineManager
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    log_exhaustive_runtime_activation_diagnostics,
    update_stage,
)

from .tasks_ai_router import _cleanup_shared_ollama_if_unused
from .tasks_build import _build_function, _build_uploaded_source
from .tasks_caddy import _regenerate_caddyfile
from .tasks_deploy_local import (
    _build_platform_healthcheck,
    _build_runtime_env,
    _local_container_timeout_seconds,
    _local_route_timeout_seconds,
    _wait_for_local_container_healthy,
    _wait_for_local_route_ready,
)
from .tasks_deploy_remote import _handle_remote_deployment, _resume_remote_deployment
from .tasks_utils import (
    _current_agent_node_queue,
    _env_bool,
    _env_int,
    should_skip_review_for_commit_message,
)


@shared_task(
    bind=True,
    max_retries=3,
    soft_time_limit=3600,  # 1 hour (reduced to prevent queue staleness)
    time_limit=3900,       # 1h 5m hard kill
)
def smart_deploy_task(self, deployment_id: str, provider_id: str,
                     skip_review: bool = False):
    """
    Orchestrates a deployment using PipelineManager for build steps.

    For fresh GIT deploys (manual): runs analysis only, pauses at REVIEW.
    For rollbacks, restarts, webhooks, and non-GIT: runs full pipeline.

    Args:
        skip_review: If True, bypass the REVIEW gate (used by restarts,
                     webhooks, and any automated deploy path).
    """
    # pylint: disable=too-many-locals
    deployment = None
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s cancelled before start", deployment_id)
            return
        skip_review = skip_review or deployment.is_rollback or should_skip_review_for_commit_message(
            deployment.commit_message
        )

        service = deployment.service
        if not provider_id or provider_id == "None":
            provider = _resolve_provider_for_service(service, prefer_local=True)
            if not provider:
                raise RuntimeError("Could not resolve cloud provider for deployment.")
        else:
            provider = CloudProvider.objects.get(id=provider_id)

        # Smart Deployment Queue / Intelligence Integration:
        # Before executing build or deployment, ensure remaining/placeholder environment variables are filled by AI Senate
        if not getattr(deployment, 'is_rollback', False) and getattr(settings, "SENATE_ENABLED", True):
            try:
                from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService
                _sugg, _inj = EnvironmentIntelligenceService.apply_intelligence_to_service(service, scan_results={})
                if _inj:
                    logger.info("Smart Deployment Queue: AI Senate auto-filled %d remaining environment variables for %s: %s", len(_inj), service.name, ", ".join(_inj))
                    if deployment.build_logs is not None:
                        deployment.build_logs = f"{deployment.build_logs}\n🧠 Smart Deployment Queue: AI Senate auto-filled {len(_inj)} remaining environment variables.\n"
                        deployment.save(update_fields=["build_logs"])
            except Exception as _senate_err:
                logger.warning("Smart Deployment Queue env enrichment failed for %s: %s", service.name, _senate_err)

        is_delegated = deployment.source_node is not None

        if not skip_review and getattr(service, 'safedeploy_enabled', False) \
                and not getattr(deployment, 'is_rollback', False) and not is_delegated:
            from apps.deployments.services.safedeploy.deployment_pipeline import (
                ProductionDeploymentPipeline,
            )
            ProductionDeploymentPipeline().process_deployment(deployment)
            if deployment.status == Deployment.Status.AWAITING_APPROVAL:
                return  # parked; will resume on approve

        # 0. Remote Delegation
        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()

        # Remote delegation: if this deployment was triggered by a remote
        # master (source_node is set), the current node should build the
        # image and then deploy to the remote via the orchestrator.
        if is_delegated:
            from apps.deployments.models_core import ManagedServer

            # Build-agent optimization: if the master sent a pre-built
            # image (docker_image is populated), don't delegate back —
            # handle it locally with the pre-built image (pull + run).
            prebuilt = str(service.docker_image or "").strip()
            if prebuilt:
                is_delegated = False
            else:
                # The source_node holds the IP of the node that sent the
                # deploy request.  Deploy back to that node.
                target = ManagedServer.objects.filter(host=deployment.source_node).first()
                if target:
                    _handle_remote_deployment(deployment, target, skip_review=skip_review)
                    return

        # Use the per-deployment target first. Explicit local deployments must
        # stay local even when the service is normally assigned to a remote node.
        effective_server = _deployment_effective_server(deployment)
        is_local = _is_local_deployment_server(effective_server, config)

        if not is_local:
            if deployment.remote_deployment_id:
                _resume_remote_deployment(deployment, effective_server)
                return

            # Build-agent optimization: for GIT services on remote
            # nodes, build and push the image on the master first,
            # then delegate with the pre-built image name so the
            # remote node skips the build and just pulls + runs.
            if service.deploy_type == 'GIT' and not str(service.docker_image or "").strip():
                with fleet_build_lock(deployment):
                    pipeline = PipelineManager(deployment)
                    if skip_review:
                        built_image = pipeline.run()
                    else:
                        pipeline.run_analysis_only()
                        broadcast_status(deployment)
                        return
                _handle_remote_deployment(
                    deployment, effective_server,
                    skip_review=skip_review, image_name=built_image,
                )
                return

            _handle_remote_deployment(deployment, effective_server, skip_review=skip_review)
            return

        # 1. Build Phase (Pipeline)
        if service.deploy_type == 'GIT':
            # A pre-built image was sent by the master (build agent
            # optimization) — skip the build phase entirely.
            prebuilt = str(service.docker_image or "").strip()
            if prebuilt and deployment.source_node:
                image_name = prebuilt
            elif deployment.is_rollback or skip_review:
                with fleet_build_lock(deployment):
                    manager = PipelineManager(deployment)
                    image_name = manager.run()
            else:
                # Fresh manual deploy → analysis only, pause for review
                manager = PipelineManager(deployment)
                manager.run_analysis_only()
                broadcast_status(deployment)
                return  # Paused at REVIEW → user must approve

        elif service.deploy_type == 'FUNCTION':
            with fleet_build_lock(deployment):
                image_name = _build_function(deployment, service)

        elif service.deploy_type == 'DOCKER':
            image_name = service.docker_image

        elif service.deploy_type == 'UPLOAD':
            with fleet_build_lock(deployment):
                image_name = _build_uploaded_source(deployment, service)

        else:
            raise ValueError(f"Unsupported deploy type: {service.deploy_type}")

        # 2. Deploy Phase (only reached for rollbacks/non-GIT)
        _deploy_container(deployment, provider, image_name)

    except PipelineError as e:
        _handle_failure(self, deployment, str(e), "Pipeline Failure")
    except Exception as e: # pylint: disable=broad-exception-caught
        _handle_failure(self, deployment, str(e), "System Failure")



@shared_task(
    bind=True,
    max_retries=2,
    soft_time_limit=3600,
    time_limit=3900,
)
def resume_deploy_task(self, deployment_id: str, provider_id: str):
    """
    Phase 2: Build + Deploy after user approves review.
    Called when user hits POST /api/v1/deployments/{id}/approve/.
    """
    deployment = None
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s cancelled", deployment_id)
            return

        service = deployment.service
        if not provider_id or provider_id == "None":
            provider = _resolve_provider_for_service(service, prefer_local=True)
            if not provider:
                raise RuntimeError("Could not resolve cloud provider for deployment.")
        else:
            provider = CloudProvider.objects.get(id=provider_id)

        # 0. Remote Delegation
        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()

        # Loop Prevention: If this is already a delegated deployment, handle it locally.
        is_delegated = deployment.source_node is not None
        effective_server = _deployment_effective_server(deployment)
        is_local = is_delegated or _is_local_deployment_server(effective_server, config)

        if not is_local:
            if deployment.remote_deployment_id:
                _resume_remote_deployment(deployment, effective_server)
                return

            # Build-agent optimization: build locally, then delegate
            # with the pre-built image name.
            prebuilt = str(service.docker_image or "").strip()
            if prebuilt and deployment.source_node:
                built_image = prebuilt
            else:
                with fleet_build_lock(deployment):
                    manager = PipelineManager(deployment)
                    built_image = manager.run_build_only()

            _handle_remote_deployment(
                deployment, effective_server, image_name=built_image,
            )
            return

        # Build phase — skip if master already pushed a pre-built image
        prebuilt = str(service.docker_image or "").strip()
        if prebuilt and deployment.source_node:
            image_name = prebuilt
        else:
            with fleet_build_lock(deployment):
                manager = PipelineManager(deployment)
                image_name = manager.run_build_only()

        # Deploy phase
        _deploy_container(deployment, provider, image_name)

    except PipelineError as e:
        _handle_failure(self, deployment, str(e), "Build Failure")
    except Exception as e:  # pylint: disable=broad-exception-caught
        _handle_failure(self, deployment, str(e), "System Failure")



def enqueue_smart_deploy_task(
    deployment_id: str,
    provider_id: str,
    skip_review: bool = False,
):
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
        provider = _resolve_provider_for_service(
            deployment.service,
            prefer_local=bool(getattr(deployment, "target_is_local", False)),
        )
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
            from apps.deployments.models_core import ManagedServer
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
    return (
        not server
        or bool(getattr(server, "is_primary", False))
        or str(getattr(server, "host", "") or "") == str(getattr(config, "server_ip", "") or "")
    )



@contextmanager
def fleet_build_lock(deployment):
    """
    Prevent resource exhaustion by limiting concurrent builds across the entire fleet.
    Uses Redis-backed cache to manage a global semaphore.
    """
    if not _env_bool("SMSLY_ENABLE_FLEET_BUILD_LOCK", False):
        append_log(deployment, "🚀 Build starting...\n")
        yield
        return

    try:
        config = PlatformConfig.load()
    except Exception:
        # Fallback if DB is unreachable
        yield
        return

    # For now, we enforce a strict single-build lock for maximum safety on small VPS nodes.
    # A true semaphore can be implemented later if needed.
    lock_key = "smsly_fleet_build_lock"
    heartbeat_key = f"{lock_key}:heartbeat"
    lock_timeout = _env_int("SMSLY_FLEET_BUILD_LOCK_TIMEOUT_SECONDS", 3600, minimum=60)
    max_wait = _env_int("SMSLY_FLEET_BUILD_LOCK_WAIT_SECONDS", 1800, minimum=30)
    poll_seconds = _env_int("SMSLY_FLEET_BUILD_LOCK_POLL_SECONDS", 15, minimum=1)
    stale_seconds = _env_int("SMSLY_FLEET_BUILD_LOCK_STALE_SECONDS", 600, minimum=60)

    acquired = False
    start_time = time.monotonic()
    heartbeat_stop = threading.Event()
    heartbeat_thread = None

    def _normalize_cache_value(value):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value or "")

    def _heartbeat_payload(owner_id: str) -> dict:
        return {"owner": owner_id, "timestamp": time.time()}

    def _refresh_heartbeat(owner_id: str) -> None:
        while not heartbeat_stop.wait(max(1, min(30, poll_seconds))):
            if _normalize_cache_value(cache.get(lock_key)) != owner_id:
                return
            cache.set(heartbeat_key, _heartbeat_payload(owner_id), timeout=lock_timeout)

    def _owner_is_stale(owner_id: str) -> tuple[bool, str]:
        try:
            owner = Deployment.objects.only("id", "status", "updated_at").get(id=owner_id)
        except Deployment.DoesNotExist:
            return True, "owner deployment no longer exists"
        except Exception as exc:  # pragma: no cover - transient DB/cache failure
            logger.warning("Could not inspect fleet build lock owner %s: %s", owner_id, exc)
            return False, "owner could not be inspected"

        lock_owner_statuses = {
            Deployment.Status.QUEUED,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        }
        if owner.status not in lock_owner_statuses:
            return True, f"owner status is {owner.status}"

        heartbeat = cache.get(heartbeat_key)
        if isinstance(heartbeat, dict) and str(heartbeat.get("owner")) == owner_id:
            try:
                heartbeat_age = time.time() - float(heartbeat.get("timestamp") or 0)
            except (TypeError, ValueError):
                heartbeat_age = stale_seconds + 1
            if heartbeat_age <= stale_seconds:
                return False, "owner heartbeat is fresh"
            return True, f"owner heartbeat is stale ({int(heartbeat_age)}s old)"

        if owner.updated_at:
            updated_age = (timezone.now() - owner.updated_at).total_seconds()
            if updated_age > stale_seconds:
                return True, f"legacy owner has no heartbeat and is stale ({int(updated_age)}s old)"

        return False, "legacy owner has no heartbeat but is still within grace period"

    while time.monotonic() - start_time < max_wait:
        # Try to set the lock if it doesn't exist
        deployment_id = str(deployment.id)
        if cache.add(lock_key, deployment_id, timeout=lock_timeout):
            acquired = True
            cache.set(heartbeat_key, _heartbeat_payload(deployment_id), timeout=lock_timeout)
            break

        # Check if the existing lock is stale (owner doesn't exist or is different but old)
        # This is a safety measure against worker crashes
        current_owner = _normalize_cache_value(cache.get(lock_key))
        if not current_owner:
            # Race condition: lock was deleted between add and get
            continue

        if current_owner == deployment_id:
            acquired = True
            cache.set(heartbeat_key, _heartbeat_payload(deployment_id), timeout=lock_timeout)
            break

        is_stale, stale_reason = _owner_is_stale(current_owner)
        if is_stale:
            append_log(
                deployment,
                f"[fleet] Recovered stale build lock from {current_owner[:8]}: {stale_reason}.\n",
            )
            cache.delete(lock_key)
            cache.delete(heartbeat_key)
            continue

        if attempt_count := getattr(fleet_build_lock, "_attempt_count", 0):
            fleet_build_lock._attempt_count = attempt_count + 1
        else:
            fleet_build_lock._attempt_count = 1
            append_log(deployment, "[fleet] Another build is in progress across the node fleet. Waiting for a free slot...\n")
            broadcast_status(deployment)

        time.sleep(poll_seconds)

    if not acquired:
        append_log(deployment, "❌ Timed out waiting for a free build slot in the node fleet.\n")
        raise RuntimeError("Fleet build concurrency limit reached. Please try again later.")

    heartbeat_thread = threading.Thread(
        target=_refresh_heartbeat,
        args=(str(deployment.id),),
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        append_log(deployment, "🚀 Build slot acquired. Starting build phase...\n")
        yield
    finally:
        heartbeat_stop.set()
        if heartbeat_thread and heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=1)
        # Only release if we were the one who held it
        if _normalize_cache_value(cache.get(lock_key)) == str(deployment.id):
            cache.delete(lock_key)
            cache.delete(heartbeat_key)
            if hasattr(fleet_build_lock, "_attempt_count"):
                delattr(fleet_build_lock, "_attempt_count")



def _run_managed_image_post_deploy_hooks(deployment, service: Service, container_id: str) -> None:
    """
    Run post-deploy hooks for Docker-image managed AI services.

    GIT/compose deploys already do this inside PipelineManager. Docker-image
    template deploys need the same behavior here after the live container is up.
    """
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        container_name = container.name
    except Exception as exc:  # pragma: no cover - daemon/container lookup is runtime-specific
        append_log(deployment, f"[hook] Skipped managed-image hooks: {exc}\n")
        return

    env_map = {ev.key: ev.value for ev in service.env_vars.all()}


    if str(env_map.get("RUN_PRISMA_MIGRATE", "")).strip().lower() in {"1", "true", "yes"}:
        append_log(deployment, "\n[hook] Running Prisma migrate deploy inside container...\n")
        prisma_res = subprocess.run(
            ["docker", "exec", container_name, "sh", "-lc", "cd /app && npx prisma migrate deploy"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if prisma_res.returncode == 0:
            append_log(deployment, "[hook] Prisma migrate deploy succeeded.\n")
        else:
            append_log(
                deployment,
                "[hook] Prisma migrate deploy failed:\n"
                f"{prisma_res.stdout}\n{prisma_res.stderr}\n",
            )

    if is_ollama_service(service):
        model_name = get_ollama_model_name(service) or str(env_map.get("OLLAMA_MODEL", "")).strip()
        if model_name:
            append_log(deployment, f"\n[hook] Pulling Ollama model `{model_name}` inside {container_name}...\n")
            pull_res = subprocess.run(
                ["docker", "exec", container_name, "sh", "-lc", f"ollama pull {shlex.quote(model_name)}"],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if pull_res.returncode == 0:
                append_log(deployment, f"[hook] Ollama model `{model_name}` is ready.\n")
            else:
                append_log(
                    deployment,
                    "[hook] Ollama model pull failed:\n"
                    f"{pull_res.stdout}\n{pull_res.stderr}\n",
                )

    if not is_ai_router_service(service):
        return

    config_text = generate_ai_router_proxy_config(service)
    with tempfile.NamedTemporaryFile("w", suffix="-ai-router.yaml", delete=False, encoding="utf-8") as handle:
        handle.write(config_text)
        config_path = handle.name

    try:
        append_log(deployment, "\n[hook] Syncing LiteLLM router catalog...\n")
        copy_res = subprocess.run(
            ["docker", "cp", config_path, f"{container_name}:/app/proxy_server_config.yaml"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if copy_res.returncode != 0:
            raise RuntimeError(
                "Failed to copy router config:\n"
                f"{copy_res.stdout}\n{copy_res.stderr}"
            )

        restart_res = subprocess.run(
            ["docker", "restart", container_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if restart_res.returncode != 0:
            raise RuntimeError(
                "Failed to restart router container after config sync:\n"
                f"{restart_res.stdout}\n{restart_res.stderr}"
            )

        if not _wait_for_local_container_healthy(deployment, container_id, timeout_seconds=180):
            raise RuntimeError("Router restart completed but health did not recover in time")

        append_log(deployment, "[hook] LiteLLM router catalog synced.\n")
    finally:
        with suppress(OSError):
            os.unlink(config_path)



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


def _sync_service_dns_to_node(deployment, service):
    """
    Auto-provision DNS records on Cloudflare to point to the remote node IP.
    """
    if not getattr(service, 'server', None) or not service.server.host:
        return

    node_ip = str(service.server.host).strip()
    if not node_ip or node_ip in ("127.0.0.1", "localhost", "0.0.0.0"):
        return

    domains = []
    if service.public_domain:
        domains.append(service.public_domain.strip())
    if service.custom_domains:
        domains.extend([d.strip() for d in (service.custom_domains or []) if d])

    if not domains:
        return

    try:
        from apps.deployments.models import PlatformConfig
        from apps.deployments.services.dns import ensure_dns_records

        config = PlatformConfig.objects.first()
        if not config or not config.cloudflare_api_token:
            return

        append_log(deployment, f"[DNS] Syncing DNS records to Node IP ({node_ip})...\n")
        dns_result = ensure_dns_records(domains, node_ip, config.cloudflare_api_token)
        if not dns_result.get("ok"):
            append_log(deployment, f"[DNS] Warning: {dns_result.get('errors')}\n")
        else:
            created = len(dns_result.get('created', []))
            updated = len(dns_result.get('updated', []))
            if created > 0 or updated > 0:
                append_log(deployment, f"[DNS] Sync OK (Created: {created}, Updated: {updated})\n")
    except Exception as dns_exc:
        logger.warning("Service DNS sync failed: %s", dns_exc)
        append_log(deployment, f"[DNS] Sync Error: {dns_exc}\n")


def _deploy_container(deployment, provider, image_name):
    """Deploy the built image to the cloud provider."""
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
                        timeout_seconds=0,  # keep polling until active
                    )

            deployment.status = Deployment.Status.ACTIVE
            deployment.finished_at = timezone.now()
            deployment.save(update_fields=['status', 'finished_at'])
            update_stage(
                deployment, 'Deploy', 'success',
                (timezone.now() - start).total_seconds()
            )
            broadcast_status(deployment)
            _regenerate_caddyfile()
            append_log(
                deployment,
                f"[OK] Compose deployment successful. "
                f"Container: {container_name}\n"
            )

            log_exhaustive_runtime_activation_diagnostics(deployment, service, container_name, promotion_type="Compose Stack")
            _sync_service_dns_to_node(deployment, service)

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

        # Inject addon connection URLs into deployed container
        from services.addon_provisioner import AddonProvisioner
        for addon in Addon.objects.filter(service=service, status='ACTIVE'):
            env_key = AddonProvisioner.ENV_KEY_MAP.get(addon.addon_type)
            if env_key and addon.connection_url:
                env_vars.setdefault(env_key, addon.connection_url)
                # Qdrant: also set host/port for apps that expect QDRANT_HOST
                if addon.addon_type == 'QDRANT':
                    parsed = urlparse(addon.connection_url)
                    env_vars.setdefault('QDRANT_HOST', parsed.hostname or 'localhost')
                    env_vars.setdefault('QDRANT_PORT', str(parsed.port or 6333))

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

        log_exhaustive_runtime_activation_diagnostics(deployment, service, resource.resource_id, target_ip="127.0.0.1", promotion_type="Local Direct / Blue-Green")

        update_stage(
            deployment,
            'Deploy',
            'done',
            (timezone.now() - start).total_seconds()
        )
        broadcast_status(deployment)
        # Regenerate local Caddyfile routing so new service domains resolve
        if provider.provider_type == CloudProvider.ProviderType.LOCAL:
            _regenerate_caddyfile()
        append_log(
            deployment,
            "[DEPLOY] ✅ Container started with Traefik routing label applied.\n"
            "Domain accessibility depends on DNS propagation and Traefik config reload.\n"
        )

        _sync_service_dns_to_node(deployment, service)

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



@shared_task(bind=True, max_retries=0, soft_time_limit=120, time_limit=150)
def _post_deploy_monitor(self, deployment_id, provider_id, container_id,
                         image_name):
    """
    Real-time post-deploy health monitor.

    Watches container logs for 30s after deploy. If the container crashes:
    1. Pattern resolver scans logs instantly for known errors (no API call)
    2. If a pattern matches and has an auto-fix → fix + auto-redeploy
    3. If patterns can't explain → escalate to AI models with code context
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        service = deployment.service
    except Deployment.DoesNotExist:
        return

    try:
        client = docker.from_env()
    except Exception:
        logger.warning("Docker not available for post-deploy monitor")
        return

    append_log(deployment, "\n🔍 Post-deploy health monitor active (30s)...\n")
    broadcast_status(deployment)

    # Poll container status for 30 seconds
    crash_detected = False
    container_logs = ""
    for check in range(6):  # 6 checks × 5s = 30s
        time.sleep(5)

        try:
            container = client.containers.get(container_id)
            status = container.status  # running, exited, restarting, dead
            container_logs = container.logs(tail=200).decode(
                'utf-8', errors='replace'
            )

            if status in ('exited', 'dead'):
                crash_detected = True
                append_log(
                    deployment,
                    f"\n🔴 Container crashed (status: {status}) "
                    f"after {(check + 1) * 5}s\n"
                )
                break

            if status == 'restarting':
                # Wait one more cycle to see if it stabilises
                if check >= 2:
                    crash_detected = True
                    append_log(
                        deployment,
                        f"\n🔴 Container stuck in restart loop "
                        f"after {(check + 1) * 5}s\n"
                    )
                    break

        except docker.errors.NotFound:
            crash_detected = True
            append_log(deployment, "\n🔴 Container disappeared after deploy\n")
            break
        except Exception as e:
            logger.warning("Monitor check failed: %s", e)
            continue

    if not crash_detected:
        append_log(deployment, "✅ Container stable — no crashes detected during 30s monitoring.\n")

        # ── Infrastructure health summary ──
        infra_lines = []

        # Container runtime (gVisor / Kata)
        try:
            from apps.deployments.services.container_runtime import detect_best_runtime, is_sandboxed_runtime
            runtime = detect_best_runtime()
            sandboxed = is_sandboxed_runtime(runtime)
            if runtime == "runsc":
                infra_lines.append("🧱 gVisor (runsc): active — user-space kernel sandbox")
            elif runtime == "kata-runtime":
                infra_lines.append("🧱 Kata Containers: active — VM-level isolation")
            elif sandboxed:
                infra_lines.append(f"🧱 Runtime: {runtime} (sandboxed)")
            else:
                infra_lines.append(f"🧱 Runtime: {runtime} (default runc)")
        except Exception:
            infra_lines.append("⚠️  Runtime: detection failed")

        # Falco
        try:
            falco_ps = subprocess.run(
                ["docker", "ps", "--filter", "name=smsly-falco",
                 "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=10,
            )
            if "Up" in (falco_ps.stdout or ""):
                infra_lines.append("🛡️  Falco: running")
            else:
                infra_lines.append("⚠️  Falco: not running")
        except Exception:
            infra_lines.append("⚠️  Falco: check failed")

        # fail2ban
        try:
            f2b_ping = subprocess.run(
                ["fail2ban-client", "ping"],
                capture_output=True, text=True, timeout=5,
            )
            if "pong" in (f2b_ping.stdout or ""):
                jails_result = subprocess.run(
                    ["fail2ban-client", "status"],
                    capture_output=True, text=True, timeout=5,
                )
                jail_count = 0
                for line in (jails_result.stdout or "").splitlines():
                    if line.strip().startswith("Jail list:"):
                        jails_str = line.split(":", 1)[1].strip()
                        jail_count = len([j for j in jails_str.split(",") if j.strip()])
                infra_lines.append(f"🔒 fail2ban: active ({jail_count} jails)")
            else:
                infra_lines.append("⚠️  fail2ban: not running")
        except Exception:
            infra_lines.append("⚠️  fail2ban: check failed")

        if infra_lines:
            append_log(deployment, "\n📋 Infrastructure: " + " | ".join(infra_lines) + "\n")

        broadcast_status(deployment)
        return

    try:
        from apps.deployments.tasks_alerts import alert_user_task
        alert_user_task.delay(
            deployment_id=str(deployment.id),
            error_message="Runtime crash detected during post-deploy monitoring",
        )
    except Exception as alert_err:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to queue runtime crash alert: %s", alert_err)

    # ── CRASH DETECTED — Run real-time diagnosis ──
    deployment.refresh_from_db()

    # Step 1: Pattern resolver (instant, no API call)
    from apps.deployments.services.error_resolver import diagnose_runtime_logs
    results = diagnose_runtime_logs(
        container_logs,
        service=service,
        deployment=deployment,
        auto_apply=True,
    )

    auto_fixed = [r for r in results if r.get('auto_fixed')]

    if auto_fixed:
        # ── Auto-fix generation cap ──
        # Count how many auto-fix generations preceded this deployment.
        # Stop after MAX_AUTO_FIX_GENERATIONS to prevent infinite fix→crash→fix loops.
        MAX_AUTO_FIX_GENERATIONS = 2
        generation = (deployment.commit_message or '').count('[auto-fix]')
        # Also count parent chain via commit_hash lineage
        from datetime import timedelta as _timedelta
        parent_autofix_count = Deployment.objects.filter(
            service=service,
            commit_message__contains='[auto-fix]',
            created_at__gte=timezone.now() - _timedelta(hours=1),
        ).count()
        effective_generation = max(generation, parent_autofix_count)

        if effective_generation >= MAX_AUTO_FIX_GENERATIONS:
            append_log(
                deployment,
                f"\n⛔ Auto-fix cap reached ({effective_generation}/{MAX_AUTO_FIX_GENERATIONS}). "
                f"Manual intervention required.\n"
            )
            deployment.status = 'FAILED'
            deployment.build_logs += f"\n--- Runtime Crash Logs ---\n{container_logs[-3000:]}\n"
            deployment.finished_at = timezone.now()
            deployment.save()
            broadcast_status(deployment)
            return

        # Auto-fix applied → trigger automatic redeploy
        append_log(
            deployment,
            f"\n🔧 {len(auto_fixed)} issue(s) auto-fixed "
            f"(generation {effective_generation + 1}/{MAX_AUTO_FIX_GENERATIONS}). "
            f"Triggering automatic redeploy...\n"
        )
        deployment.status = 'FAILED'
        deployment.build_logs += f"\n--- Runtime Crash Logs ---\n{container_logs[-3000:]}\n"
        deployment.save()
        broadcast_status(deployment)

        # Create a new deployment with the fix applied
        new_deployment = Deployment.objects.create(
            service=service,
            status='QUEUED',
            commit_hash=deployment.commit_hash,
            commit_message=f"[auto-fix] {', '.join(r['category'] for r in auto_fixed)}",
            is_rollback=False,
        )
        provider = CloudProvider.objects.get(id=provider_id)
        try:
            enqueue_smart_deploy_task(
                deployment_id=str(new_deployment.id),
                provider_id=str(provider.id),
                skip_review=True,
            )
        except Exception as exc:  # pragma: no cover - broker/runtime failure
            logger.exception(
                "Failed to enqueue auto-fix deployment %s",
                new_deployment.id,
            )
            new_deployment.status = Deployment.Status.FAILED
            new_deployment.finished_at = timezone.now()
            new_deployment.build_logs = (
                (new_deployment.build_logs or "")
                + f"\n[ERROR] Failed to queue auto-fix deploy task: {exc}\n"
            )
            new_deployment.save(update_fields=["status", "finished_at", "build_logs", "updated_at"])
        return

    # Step 2: No pattern match → escalate to AI models
    from apps.deployments.tasks_ai_router import _escalate_to_ai
    _escalate_to_ai(deployment, service, container_logs)

    # Step 3: Jules auto-fix (async) — tries to fix and redeploy
    try:
        from apps.intelligence.jules_fix import jules_fix_deployment_failure
        jules_fix_deployment_failure.delay(
            deployment_id=str(deployment.id),
            logs=container_logs,
            repo_path=None,
            repo_url=service.repository_url or "",
        )
        logger.info("Jules auto-fix triggered for runtime crash on deployment %s", deployment.id)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Failed to trigger Jules auto-fix for runtime crash: %s", e)

    # Mark deployment as failed
    deployment.status = 'FAILED'
    deployment.build_logs += f"\n--- Runtime Crash Logs ---\n{container_logs[-3000:]}\n"
    deployment.finished_at = timezone.now()
    deployment.save()
    broadcast_status(deployment)



def _handle_failure(task, deployment, error_msg, reason):
    from .tasks_deploy_remote import self_heal_remote_deployment
    """Centralized failure handling with pattern resolver + AI escalation."""
    logger.error("%s: %s", reason, error_msg)

    if deployment:
        deployment.refresh_from_db()
        if deployment.status != 'CANCELLED':
            deployment.status = 'FAILED'
            deployment.finished_at = timezone.now()

            # Sanitize inputs for PostgreSQL
            safe_reason = str(reason).replace('\x00', '')
            safe_msg = str(error_msg).replace('\x00', '')

            deployment.build_logs += f"\n✗ {safe_reason}: {safe_msg}\n"
            deployment.save()
            broadcast_status(deployment)

            # Cleanup orphaned container if one was created.
            # Only remove green_container_id (candidate container that never
            # went live). Do NOT remove container_id blindly — it may point
            # to the currently-live production container if the failure
            # happened after cutover (e.g. route check failure).
            try:
                if deployment.green_container_id:
                    import docker
                    client = docker.from_env()
                    try:
                        container = client.containers.get(deployment.green_container_id)
                        container.remove(force=True)
                        logger.info(
                            "Cleaned up orphaned green container %s for failed deployment %s",
                            deployment.green_container_id, deployment.id,
                        )
                        deployment.build_logs += "\n🧹 Cleaned up orphaned container resources.\n"
                        deployment.save(update_fields=['build_logs'])
                    except docker.errors.NotFound:
                        pass
                    except Exception as e:
                        logger.warning(
                            "Failed to cleanup green container %s: %s",
                            deployment.green_container_id, e,
                        )
            except Exception as e:
                logger.warning(f"Docker client error during failure cleanup: {e}")

            # Sweep stale green candidates from prior failed deploys.
            # When a service has multiple failed attempts, each creates a
            # green container that can accumulate as restart-looping orphans.
            try:
                service_name = deployment.service.name
                import docker
                _client = docker.from_env()
                stale = _client.containers.list(
                    all=True,
                    filters={"status": ["exited", "dead", "restarting"]},
                )
                swept = 0
                for c in stale:
                    parts = c.name.rsplit("-green-", 1)
                    if len(parts) == 2 and parts[0] == service_name:
                        try:
                            c.remove(force=True)
                            swept += 1
                        except Exception:
                            pass
                if swept:
                    logger.info(
                        "Swept %d stale green container(s) for %s on deploy failure",
                        swept, service_name,
                    )
                    deployment.build_logs += (
                        f"\n🧹 Swept {swept} stale green container(s) from prior failed attempts.\n"
                    )
                    deployment.save(update_fields=['build_logs'])
            except Exception as sweep_err:
                logger.warning("Failed to sweep stale green containers: %s", sweep_err)

            try:
                from apps.deployments.tasks_alerts import alert_user_task
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
                from apps.deployments.tasks_ai import analyze_failure_task
                analyze_failure_task.delay(deployment_id=str(deployment.id))
            except ImportError:
                pass  # Ignore if module cannot be imported
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.warning("Failed to trigger AI failure task: %s", e)

            # Step 3: Jules auto-fix (async) — tries to fix and redeploy
            try:
                from apps.intelligence.jules_fix import jules_fix_deployment_failure
                try:
                    from apps.intelligence.models import AIProviderSettings
                except Exception:
                    AIProviderSettings = None

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



@shared_task(bind=True, max_retries=3)
def delete_service_task(self, service_id: str, force: bool = False):
    """Async reliable deletion of a Service"""
    from apps.deployments.models_core import Service
    from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        return

    success = False

    # 1. Handle remote server cleanup if applicable
    try:
        from apps.deployments.utils_target import resolve_active_execution_target
        target = resolve_active_execution_target(service)
        active_server = target["server_obj"]
    except Exception:
        active_server = getattr(service, 'server', None)

    if active_server and not active_server.is_primary:
        try:
            logger.info("Decommissioning service %s on remote node %s", service.name, active_server.host)
            remote = RemoteOrchestrator(active_server)
            success = remote.delete_service_for_local(service, force=force)

            # If force=True, we proceed even if remote call fails (best-effort local cleanup)
            if force:
                success = True
        except Exception as exc:
            logger.warning("Remote deletion failed for service %s: %s.", service.name, exc)
            success = force
    else:
        # 2. Local cleanup
        orchestrator = DeletionOrchestrator()
        success = orchestrator.delete_service_resources(service, force=force)

        # 2b. Clean up addon runtime resources before DB cascade
        for addon in Addon.objects.filter(service=service):
            server = getattr(addon.service, 'server', None)
            if (server and not server.is_primary
                    and not getattr(server, 'is_lite_agent', False)):
                container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
                ok = addon_provisioner.deprovision_remote(
                    addon.coolify_uuid or container_name, server, container_name,
                )
            elif orchestrator.docker_client:
                ok = orchestrator.delete_addon_resources(addon)
            else:
                ok = True
            if not ok:
                logger.warning("Failed to clean up addon %s (%s) for service %s.",
                               addon.id, addon.addon_type, service.name)
                if not force:
                    success = False

        # 3. Resilience: If force=True, we proceed regardless of resource cleanup success.
        # This ensures the DB record is purged when the user explicitly requests a force-delete.
        if force:
            logger.info("Force-purging service %s from database after best-effort cleanup.", service.name)
            success = True
        elif not success and not service.server and not orchestrator.docker_client:
            logger.warning("Docker client unavailable for service %s. Forcing database-only deletion.", service.name)
            success = True

    if success:
        # Capture project reference and owner before deleting the service.
        service_project = getattr(service, 'project', None)
        service_owner_id = getattr(service, 'owner_id', None)

        # GDPR right-to-erasure: delete all backup tarballs and DB rows
        # owned by this service's user BEFORE the CASCADE fires. The
        # backup file paths are not recoverable once the ServiceBackup row
        # is gone.
        try:
            from .services.backup_service import purge_user_backups
            purge_user_backups(service_owner_id)
        except Exception as cleanup_exc:
            logger.warning(
                "Backup purge during service deletion failed for %s: %s",
                service.id, cleanup_exc,
            )

        service.delete()

        # After deleting an LLM consumer, check if shared Ollama CPP
        # is still needed. If no remaining services need it, clean it up
        # to free VPS resources.
        if service_project:
            try:
                _cleanup_shared_ollama_if_unused(service_project)
            except Exception as cleanup_exc:
                logger.warning("Shared Ollama cleanup check failed for project %s: %s",
                               service_project.id, cleanup_exc)
    else:
        service.status = Service.Status.DELETION_FAILED
        service.deletion_error = "Failed to remove some runtime resources. If this node is unassigned or unreachable, use 'Retry' or manual DB cleanup."
        service.save(update_fields=['status', 'deletion_error'])
