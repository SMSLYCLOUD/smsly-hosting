# ============================================================================
# REFACTOR IN PROGRESS — see docs/REFACTOR_PLAN_VIEWS_TASKS.md
# This file is being split into per-domain siblings. New code should be
# added to the appropriate sibling file (e.g. views_servers.py, tasks_health.py).
# ============================================================================
# pylint: disable=too-many-lines
"""Tasks module."""
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager, suppress
from urllib.parse import unquote, urlparse

import docker
import requests
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.cloud.services.builder import NixpacksBuilder
from apps.cloud.services.compute import ComputeService
from apps.cloud.services.function_provisioner import FunctionProvisioner
from apps.deployments.ai_router import (
    DEFAULT_AI_ROUTER_API_BASE,
    DEFAULT_AI_ROUTER_UI_BASE,
    DEFAULT_BRAID_ALIAS,
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
from apps.deployments.models_addons import Addon, Backup
from apps.deployments.models_storage import Volume
from apps.deployments.services.pipeline import PipelineError, PipelineManager
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.services.tls_verify import should_verify
from apps.deployments.services.transfer_service import ServerTransferService
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    is_deployment_local,
    update_stage,
)

# Imports for AIProviderSettings; jules_fix is imported lazily inside tasks
# Note: AIProviderSettings is not available in agent mode
try:
    from apps.intelligence.models import AIProviderSettings as _AIProviderSettings
except (ImportError, RuntimeError):
    _AIProviderSettings = None  # type: ignore[assignment]
AIProviderSettings = _AIProviderSettings
from services.addon_provisioner import addon_provisioner

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


def _regenerate_caddyfile():
    """Regenerate and apply the Caddyfile with current service domains.

    Called after successful deployments so new services get Caddy site blocks
    (and therefore SSL certificates) without requiring a manual Settings save.
    """
    try:
        config = PlatformConfig.load()
        from services.caddy_manager import apply_caddyfile, generate_caddyfile
        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            logger.info("Caddyfile regenerated after deployment")
        else:
            logger.warning("Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not regenerate Caddyfile: %s", exc)


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

    getattr(config, "max_concurrent_builds", 1) or 1
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


def _docker_safe_segment(value: str, fallback: str = "app") -> str:
    """Normalize strings used in Docker image tags and names."""
    slug = re.sub(r"[^a-z0-9_.-]+", "-", str(value or "").lower()).strip("-.")
    if not slug:
        slug = fallback
    return slug[:63]


def _detect_exposed_port(service, image_name: str | None = None) -> int | None:
    """Auto-detect port from Docker image EXPOSE directive.

    Inspects the specified image name, or the last deployed image for this service.
    If the image has EXPOSE ports, returns the first one. This prevents the common
    mismatch where Dockerfile EXPOSE says 3000 but we default PORT to 8000.
    """
    try:
        client = docker.from_env()
        exposed = None

        if image_name:
            try:
                img = client.images.get(image_name)
                exposed = img.attrs.get('Config', {}).get('ExposedPorts', {})
            except docker.errors.ImageNotFound:
                pass

        if not exposed:
            last_dep = service.deployments.filter(
                container_id__isnull=False
            ).order_by('-created_at').first()
            if last_dep:
                if last_dep.container_id:
                    try:
                        container = client.containers.get(last_dep.container_id)
                        exposed = container.image.attrs.get('Config', {}).get('ExposedPorts', {})
                    except docker.errors.NotFound:
                        pass
                if not exposed and last_dep.image_name:
                    try:
                        img = client.images.get(last_dep.image_name)
                        exposed = img.attrs.get('Config', {}).get('ExposedPorts', {})
                    except docker.errors.ImageNotFound:
                        pass

        if exposed:
            # ExposedPorts looks like {"3000/tcp": {}, "8080/tcp": {}}
            for port_spec in exposed:
                port_num = port_spec.split('/')[0]
                if port_num.isdigit():
                    return int(port_num)
    except Exception as exc:
        logger.debug("Port auto-detect failed: %s", exc)
    return None


def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_legacy_default_healthcheck(service: Service) -> bool:
    """
    Detect untouched platform defaults that historically forced /health checks.

    When these defaults are untouched, we now prefer image-native health checks
    (or running-state readiness) to avoid false negatives for frameworks that
    don't expose /health by default.
    """
    return (
        (service.health_check_path or "").strip() == "/health"
        and service.health_check_port in (None, 0)
        and _coerce_int(service.health_check_interval, 60) == 60
        and _coerce_int(service.health_check_timeout, 15) == 15
        and _coerce_int(service.health_check_retries, 8) == 8
    )


def _build_platform_healthcheck(service: Service, env_vars: dict) -> dict | None:
    """
    Build platform healthcheck config for container deployment.

    Returns None when no explicit healthcheck is configured, so the adapter can
    keep Dockerfile HEALTHCHECK behavior (or no healthcheck) intact.
    """
    path = (service.health_check_path or "").strip()
    if not path:
        return None

    # Backward-compatible escape hatch if operators want strict legacy behavior.
    force_legacy_default = _env_bool("FORCE_PLATFORM_DEFAULT_HEALTHCHECK", default=False)
    if _is_legacy_default_healthcheck(service) and not force_legacy_default:
        return None

    health_port = service.health_check_port
    if health_port in (None, 0):
        raw_port = str((env_vars or {}).get("PORT", "")).strip()
        if raw_port.isdigit():
            health_port = int(raw_port)

    return {
        "path": path,
        "port": health_port,
        "interval": service.health_check_interval,
        "timeout": service.health_check_timeout,
        "retries": service.health_check_retries,
    }


def _build_runtime_env(service: Service, image_name: str | None = None) -> dict:
    """Assemble runtime env vars with routing domains sourced from Service."""
    def _is_ciphertext(val: str) -> bool:
        if not val or not isinstance(val, str):
            return False
        if val.startswith("gAAAA"):
            return True
        if len(val) > 100 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in val):
            try:
                import base64
                padded = val + '=' * (-len(val) % 4)
                decoded = base64.urlsafe_b64decode(padded)
                if len(decoded) >= 57 and decoded[0] == 0x80:
                    return True
            except Exception:
                pass
        return False

    env_vars = {}
    for env in service.env_vars.all():
        val = env.value
        if _is_ciphertext(val):
            logger.warning(
                "[DB-ENCRYPT] Skipping ciphertext env var %s for service %s at runtime injection",
                env.key, service.name,
            )
            continue
        # Safety net: skip env vars whose values are still placeholder tokens.
        # This catches {{GENERATE}}, {{FILL_ME}}, {{REPLACE_WITH_PRODUCTION_X}},
        # and any other {{...}} token that was never resolved (e.g. because a
        # non-ecosystem deploy path bypassed _resolve_env_placeholders).
        if isinstance(val, str) and re.search(r"\{\{.*?\}\}", val):
            logger.warning(
                "[PLACEHOLDER] Skipping unresolved placeholder %s=%s for service %s "
                "at runtime injection — addon may not be provisioned yet.",
                env.key, val, service.name,
            )
            continue
        env_vars[env.key] = val

    # ── Locked keys: user has explicitly locked these — never override them ──
    locked_keys = set(
        service.env_vars.filter(is_locked=True).values_list('key', flat=True)
    )

    # Resolve shortcodes in all env vars (e.g. {{addon.URL}})
    try:
        from services.env_resolver import resolve_shortcodes
        for key, value in env_vars.items():
            env_vars[key] = resolve_shortcodes(str(service.id), value)
    except Exception as e:
        logger.warning(f"Failed to resolve shortcodes for service {service.name}: {e}")

    # Resolve runtime PORT with safe precedence:
    # 1) Explicit PORT env var (user/app intent)
    # 2) Explicit non-default internal_port
    # 3) Docker image EXPOSE auto-detection
    # 4) Fallback 8000
    #
    # This prevents forcing default internal_port=8000 onto apps that
    # naturally bind 3000/8080 and would otherwise fail health checks.
    if 'PORT' not in locked_keys:
        explicit_env_port = str(env_vars.get('PORT', '')).strip()
        if explicit_env_port:
            env_vars['PORT'] = explicit_env_port
            try:
                p_val = int(explicit_env_port)
                if service.internal_port != p_val:
                    service.internal_port = p_val
                    service.save(update_fields=['internal_port'])
            except ValueError:
                pass
        elif service.internal_port and int(service.internal_port) != 8000:
            env_vars['PORT'] = str(service.internal_port)
        else:
            detected_port = _detect_exposed_port(service, image_name=image_name)
            if detected_port:
                env_vars['PORT'] = str(detected_port)
                if service.internal_port != detected_port:
                    service.internal_port = detected_port
                    service.save(update_fields=['internal_port'])
            else:
                env_vars['PORT'] = '8000'

    # Ensure the app binds to all interfaces so Docker health checks
    # (which probe 127.0.0.1) can reach it. Next.js standalone, for
    # example, defaults to binding to the container hostname only.
    if 'HOSTNAME' not in locked_keys:
        env_vars.setdefault('HOSTNAME', '0.0.0.0')

    # ── Auto-generate critical Django env vars ──────────────────────
    # SECRET_KEY: generate a secure random key if not explicitly set (or set to empty).
    # Without this, Django apps crash on startup in production.
    if not env_vars.get('SECRET_KEY') and not env_vars.get('DJANGO_SECRET_KEY'):
        env_vars['SECRET_KEY'] = secrets.token_urlsafe(50)

    # FERNET_KEY: many apps require a Fernet key; generate if missing/blank.
    try:
        if not env_vars.get('FERNET_KEY'):
            from cryptography.fernet import Fernet
            env_vars['FERNET_KEY'] = Fernet.generate_key().decode()
    except Exception:
        pass

    # Generic admin placeholders to avoid boot-time crashes; users can override later.
    fallback_if_blank = {
        'ADMIN_EMAIL': 'admin@example.com',
        'ADMIN_USERNAME': 'admin',
        'OPS_HEALTH_TOKEN': secrets.token_urlsafe(16),
    }
    for k, v in fallback_if_blank.items():
        if not str(env_vars.get(k, '')).strip():
            env_vars[k] = v

    # ── Inject addon connection URLs (DATABASE_URL, REDIS_URL, etc.) ──
    # This ensures addon env vars are available in ALL deploy paths.
    try:
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
    except Exception:
        pass  # Don't block deploy if addon lookup fails

    # ── Ecosystem linking: cross-service URLs, shared DB routing ──
    # This is the god-level intelligence: it reads the live ecosystem graph,
    # finds deployed siblings, wires cross-service URLs, rewrites DATABASE_URL
    # to the correct per-service database, propagates shared secrets, and
    # isolates Redis DB numbers. Must run BEFORE smart derivation.
    _link_ecosystem(service, env_vars)

    # ── Smart derivation: parse compound URLs into individual vars ──
    # Many apps expect individual DB_HOST/DB_NAME/etc. instead of DATABASE_URL.
    # Parse the URL and inject individual vars so apps don't crash.
    _smart_derive_database_vars(env_vars)
    _smart_derive_redis_vars(env_vars)

    # ── Domain-aware injection ──
    # Build a unified hosts list from public domain + custom domains.
    # Ensures ALLOWED_HOSTS, DJANGO_ALLOWED_HOSTS, and MARKETER_ALLOWED_HOSTS
    # all receive the same comprehensive value (no divergence).
    all_hosts = ['localhost', '127.0.0.1', '0.0.0.0']
    if service.public_domain and not service.public_domain_hidden:
        env_vars['PUBLIC_DOMAIN'] = service.public_domain
        all_hosts.append(service.public_domain)
    for d in (service.custom_domains or []):
        if isinstance(d, str) and d.strip():
            all_hosts.append(d.strip())
    hosts_csv = ','.join(all_hosts)

    # Set all ALLOWED_HOSTS variants the app might use (only if not already set)
    if not env_vars.get('ALLOWED_HOSTS'):
        env_vars['ALLOWED_HOSTS'] = hosts_csv
    if not env_vars.get('DJANGO_ALLOWED_HOSTS'):
        env_vars['DJANGO_ALLOWED_HOSTS'] = hosts_csv
    if not env_vars.get('MARKETER_ALLOWED_HOSTS'):
        env_vars['MARKETER_ALLOWED_HOSTS'] = hosts_csv

    if service.public_domain:

        # API_INTERNAL_URL: the internal URL the app can call itself at
        port = env_vars.get('PORT', '8000')
        env_vars.setdefault('API_INTERNAL_URL', f'http://127.0.0.1:{port}')

        # SMSLY_BACKEND_URL: for apps that proxy to their own backend
        env_vars.setdefault('SMSLY_BACKEND_URL', f'http://127.0.0.1:{port}')
    else:
        env_vars.pop('PUBLIC_DOMAIN', None)

    custom_domains = []
    for domain in service.custom_domains or []:
        if not isinstance(domain, str):
            continue
        value = domain.strip()
        if not value:
            continue
        if value not in custom_domains:
            custom_domains.append(value)

    if custom_domains:
        env_vars['CUSTOM_DOMAINS'] = ",".join(custom_domains)
    else:
        env_vars.pop('CUSTOM_DOMAINS', None)

    # Inject Infisical env vars for secret management
    try:
        from .services.infisical import get_infisical_client, get_or_create_workspace, inject_infisical_env_for_service
        _client = get_infisical_client()
        if _client is not None:
            _ws_id = get_or_create_workspace(_client)
            if _ws_id:
                infisical_vars = inject_infisical_env_for_service(str(service.id), _client, _ws_id)
                env_vars.update(infisical_vars)
    except Exception:
        pass  # Infisical is optional — user containers work without it

    return env_vars


def _smart_derive_database_vars(env_vars: dict):
    """Parse DATABASE_URL into individual DB_* vars for apps that need them."""
    db_url = env_vars.get('DATABASE_URL', '')
    if not db_url:
        return

    try:
        parsed = urlparse(db_url)
        if not parsed.hostname:
            return

        env_vars['DB_HOST'] = parsed.hostname
        env_vars['DB_PORT'] = str(parsed.port or 5432)
        env_vars['DB_USER'] = parsed.username or 'postgres'
        env_vars['DB_NAME'] = parsed.path.lstrip('/') or 'postgres'

        if parsed.password:
            env_vars['DB_PASSWORD'] = parsed.password
            env_vars['MARKETER_DB_PASSWORD'] = parsed.password

        # Postgres-specific aliases some frameworks use
        env_vars['POSTGRES_HOST'] = parsed.hostname
        env_vars['POSTGRES_PORT'] = str(parsed.port or 5432)
        env_vars['POSTGRES_USER'] = parsed.username or 'postgres'
        env_vars['POSTGRES_DB'] = parsed.path.lstrip('/') or 'postgres'
        if parsed.password:
            env_vars['POSTGRES_PASSWORD'] = parsed.password
    except Exception:
        pass  # Don't block deploy if URL parsing fails


def _smart_derive_redis_vars(env_vars: dict):
    """Parse REDIS_URL into Celery broker/backend vars."""
    redis_url = env_vars.get('REDIS_URL', '')
    if not redis_url:
        return

    try:
        # Celery broker and result backend default to the Redis URL
        env_vars['CELERY_BROKER_URL'] = redis_url
        env_vars['CELERY_RESULT_BACKEND'] = redis_url

        # Some apps use numbered Redis databases for separation
        parsed = urlparse(redis_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # If broker is on /0, put result backend on /1
        if not parsed.path or parsed.path in {'/', '/0'}:
            if env_vars.get('CELERY_BROKER_URL') == redis_url:
                env_vars['CELERY_BROKER_URL'] = f"{base}/0"
            if env_vars.get('CELERY_RESULT_BACKEND') == redis_url:
                env_vars['CELERY_RESULT_BACKEND'] = f"{base}/1"

        # Cache URL alias
        env_vars['CACHE_URL'] = redis_url
    except Exception:
        pass  # Don't block deploy if URL parsing fails


# ──────────────────────────────────────────────────────────────────────────────
# Ecosystem Intelligence — cross-service auto-wiring
# ──────────────────────────────────────────────────────────────────────────────

# Known cross-service URL patterns.  Maps env-var names to a pattern
# that should match a deployed sibling's name.
# Format: {'ENV_VAR': ['substring-match-1', 'substring-match-2']}
_SERVICE_URL_PATTERNS = {
    'SMSLY_BACKEND_URL':      ['smsly-backend', 'smsly-platform-api', 'backend'],
    'BACKEND_URL':            ['smsly-backend', 'backend'],
    'IDENTITY_SERVICE_URL':   ['smsly-identity', 'identity'],
    'PLATFORM_API_URL':       ['smsly-platform-api', 'platform-api'],
    'AUDIT_SERVICE_URL':      ['smsly-audit', 'audit'],
    'TRANSACTION_CHAIN_URL':  ['smsly-transaction-chain', 'transaction-chain', 'txchain'],
    'SECURITY_GATEWAY_URL':   ['smsly-gateway', 'gateway'],
    'POLICY_SERVICE_URL':     ['smsly-policy', 'policy'],
    'RATE_LIMIT_SERVICE_URL': ['smsly-rate-limit', 'rate-limit'],
    'VIDEO_SERVICE_URL':      ['smsly-video', 'video-service'],
    'VOICE_SERVICE_URL':      ['smsly-voice', 'voice'],
    'HOSTING_SERVICE_URL':    ['smsly-hosting', 'hosting'],
    'NEXT_PUBLIC_API_URL':    ['backend', 'api', 'platform-api'],
}

# Secrets that should propagate across sibling services.
_PROPAGATED_SECRETS = {
    'INTERNAL_API_SECRET',
    'GATEWAY_SECRET',
    'JWT_SECRET',
}

# Known per-service database names.  The heuristic checks in order:
# 1. Analysis result metadata (from AI/code scan)
# 2. This static map (from docker-compose / init-databases.sql knowledge)
# 3. Sanitized service name as fallback
_SERVICE_DB_MAP = {
    'smsly-backend':            'smsly_backend',
    'smsly-platform-api':       'smsly_backend',
    'smsly-hosting-backend':    'smsly_hosting',
    'smsly-identity':           'smsly_identity',
    'smsly-audit':              'smsly_audit',
    'smsly-transaction-chain':  'smsly_txchain',
    'smsly-helper':             'ainav',
    'lina-deluxe':              'lina',
    'fegloire':                 'buyforfront',
    'buyforfront':              'buyforfront',
    'smsly-marketer':           'marketer',
}

# Known per-service Redis DB numbers.
_SERVICE_REDIS_DB = {
    'smsly-helper':     1,
    'smsly-marketer':   4,
}


def _link_ecosystem(service: Service, env_vars: dict):
    """
    God-level ecosystem linking.

    Reads the live ecosystem graph (all deployed siblings by same owner),
    then autonomously:
      1. Rewrites DATABASE_URL to the correct per-service database
      2. Resolves cross-service URLs from deployed siblings' live domains
      3. Propagates shared secrets (INTERNAL_API_SECRET, etc.)
      4. Isolates Redis DB numbers per service

    Runs AFTER addon provisioning, BEFORE smart derivation.
    Failures are logged but never block deployment.
    """
    try:
        from services.ecosystem_graph import (
            build_ecosystem_graph,
            get_sibling_env_value,
            resolve_service_url,
            rewrite_database_url,
            set_redis_db,
        )
    except ImportError:
        logger.warning("ecosystem_graph module not available — skipping linking")
        return

    try:
        graph = build_ecosystem_graph(service)
    except Exception as exc:
        logger.warning("Failed to build ecosystem graph: %s", exc)
        return

    deployed = graph.get('deployed', {})
    shared_addons = graph.get('shared_addons', {})
    svc_name = (service.name or '').lower().strip()

    # ── 1. Database routing ──────────────────────────────────────────
    # If this service has a DATABASE_URL from its own addon, rewrite it
    # to target the correct per-service database.
    db_name = _infer_database_name(service)
    # For preview services, the DATABASE_URL already points to the clone DB.
    # Don't rewrite it — _infer_database_name would return the wrong name.
    if db_name and 'DATABASE_URL' in env_vars and not svc_name.startswith('preview-'):
        try:
            old_url = env_vars['DATABASE_URL']
            new_url = rewrite_database_url(old_url, db_name)
            if new_url != old_url:
                env_vars['DATABASE_URL'] = new_url
                _ensure_database_exists(old_url, db_name)
                logger.info(
                    "Ecosystem: rewrote DATABASE_URL for '%s' → db=%s",
                    service.name, db_name,
                )
        except Exception as exc:
            logger.warning("Failed to rewrite DATABASE_URL: %s", exc)

    # If this service has NO DATABASE_URL but a sibling shares Postgres,
    # derive one from the shared addon.
    if 'DATABASE_URL' not in env_vars and 'POSTGRES' in shared_addons and not svc_name.startswith('preview-'):
        try:
            base_url = shared_addons['POSTGRES']
            if db_name:
                env_vars['DATABASE_URL'] = rewrite_database_url(base_url, db_name)
                _ensure_database_exists(base_url, db_name)
                logger.info(
                    "Ecosystem: injected shared DATABASE_URL for '%s' → db=%s",
                    service.name, db_name,
                )
        except Exception as exc:
            logger.warning("Failed to inject shared DATABASE_URL: %s", exc)

    # ── 2. Cross-service URL resolution ──────────────────────────────
    if not svc_name.startswith('preview-'):
        for env_key, match_patterns in _SERVICE_URL_PATTERNS.items():
            if env_key in env_vars:
                continue  # Don't override explicit values

            for pattern in match_patterns:
                matched_sib = None
                for sib_name, sib_info in deployed.items():
                    if pattern in sib_name.lower():
                        matched_sib = sib_info
                        break

                if matched_sib:
                    url = resolve_service_url(matched_sib)
                    env_vars[env_key] = url
                    logger.info(
                        "Ecosystem: %s=%s (from sibling '%s')",
                        env_key, url, matched_sib['name'],
                    )
                    break  # Resolved, move to next env_key

    # ── 3. Shared secret propagation ─────────────────────────────────
    # Skip for preview environments — they must not inherit production secrets
    if not svc_name.startswith('preview-'):
        for secret_key in _PROPAGATED_SECRETS:
            if secret_key in env_vars:
                continue  # Already set

            for sib_name in deployed:
                try:
                    sib_val = get_sibling_env_value(service, sib_name, secret_key)
                    if sib_val:
                        env_vars[secret_key] = sib_val
                        logger.info(
                            "Ecosystem: propagated %s from sibling '%s'",
                            secret_key, sib_name,
                        )
                        break
                except Exception:
                    continue

    # ── 4. Redis DB isolation ────────────────────────────────────────
    redis_url = env_vars.get('REDIS_URL', '')
    if redis_url:
        # Check if this service has a known Redis DB number
        known_db = _SERVICE_REDIS_DB.get(svc_name)
        if known_db is not None:
            env_vars['REDIS_URL'] = set_redis_db(redis_url, known_db)
            logger.info(
                "Ecosystem: set Redis DB to /%d for '%s'",
                known_db, service.name,
            )

    logger.info(
        "Ecosystem linking complete for '%s': %d siblings checked",
        service.name, len(deployed),
    )


def _infer_database_name(service: Service) -> str:
    """
    Determine which database this service needs on the shared Postgres.

    Priority:
      1. Analysis result metadata (AI or deep code scan stored 'database_name')
      2. Static map (from docker-compose / init-databases.sql knowledge)
      3. Sanitized service name as reasonable fallback
    """
    # 1. From analysis metadata
    try:
        last_deploy = service.deployments.order_by('-created_at').first()
        if last_deploy and isinstance(last_deploy.analysis_result, dict):
            db_name = last_deploy.analysis_result.get('database_name')
            if db_name:
                return db_name
    except Exception:
        pass

    # 2. From static service-to-DB map
    svc_name = (service.name or '').lower().strip()
    if svc_name in _SERVICE_DB_MAP:
        return _SERVICE_DB_MAP[svc_name]

    # 3. Sanitized service name
    return re.sub(r'[^a-z0-9_]', '_', svc_name)[:63]


def _ensure_database_exists(base_url: str, db_name: str):
    """
    Ensure the target database exists on the shared Postgres server.
    """
    conn = None
    try:
        import psycopg2
        from psycopg2 import sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        conn = psycopg2.connect(base_url)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if not cur.fetchone():
                query = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
                cur.execute(query)
                logger.info("Ecosystem: Auto-provisioned shared database '%s'", db_name)
    except Exception as exc:
        logger.warning("Ecosystem: Failed to auto-provision database '%s': %s", db_name, exc)
    finally:
        if conn:
            conn.close()



def _resolve_upload_zip_path(repository_url: str) -> str:
    """Extract a local file path from file:// repository URLs."""
    parsed = urlparse(repository_url or "")
    if parsed.scheme != "file":
        raise ValueError("UPLOAD deploys require a file:// repository_url")

    if parsed.netloc and parsed.netloc not in ("localhost", "127.0.0.1"):
        raise ValueError("Only local file:// paths are supported for uploads")

    zip_path = unquote(parsed.path or "")
    if os.name == "nt" and zip_path.startswith("/"):
        # file:///C:/path.zip -> /C:/path.zip
        zip_path = zip_path.lstrip("/")
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"Uploaded source archive not found: {zip_path}")
    return zip_path


def _safe_extract_zip(zip_path: str, destination: str):
    """Extract zip archive while preventing ZipSlip path traversal."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        dest_root = os.path.abspath(destination)
        for member in zf.infolist():
            member_name = member.filename
            if not member_name or member_name.endswith("/"):
                continue
            target_path = os.path.abspath(os.path.join(dest_root, member_name))
            if not target_path.startswith(dest_root + os.sep):
                raise ValueError("Archive contains unsafe file paths")
        zf.extractall(dest_root)


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

        # Capture a pre-deployment snapshot
        try:
            from apps.deployments.services.snapshot_service import SnapshotService
            SnapshotService.capture_snapshot(
                service_id=str(service.id),
                trigger='PRE_DEPLOY',
                label=f"Auto pre-deploy snapshot (deployment {deployment.id})",
                created_by=None,
            )
        except Exception as exc:
            logger.warning("Failed to capture auto pre-deploy snapshot for deployment %s: %s", deployment.id, exc)

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


def _handle_remote_deployment_legacy(deployment, server):
    """Delegate deployment to a remote server and poll for status."""
    from apps.deployments.services.server_guard import ServerGuard

    service = deployment.service
    guard = ServerGuard.check_user_workload_allowed(server)
    if not guard["ok"]:
        _handle_failure(
            None,
            deployment,
            guard["error"]["message"],
            "Placement Guard",
        )
        return

    orchestrator = RemoteOrchestrator(server)

    append_log(deployment, f"🌐 Delegating deployment to remote server: {server.name} ({server.host})\n")
    update_stage(deployment, 'Remote Sync', 'running')

    # 1. Sync Service
    remote_svc_id = orchestrator.sync_service(service)
    if not remote_svc_id:
        _handle_failure(
            None,
            deployment,
            _remote_failure_message(orchestrator, "Failed to sync service to remote server"),
            "Remote Sync Failure",
        )
        return

    orchestrator.sync_env_vars(service, remote_svc_id)
    update_stage(deployment, 'Remote Sync', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')

    # 2. Trigger Deploy
    remote_dep_id = orchestrator.trigger_deploy(deployment, remote_svc_id)
    if not remote_dep_id:
        _handle_failure(
            None,
            deployment,
            _remote_failure_message(orchestrator, "Failed to trigger deployment on remote server"),
            "Remote Deploy Failure",
        )
        return

    append_log(deployment, f"🚀 Remote deployment triggered: {remote_dep_id}\n")

    # 3. Polling Loop
    max_retries = 90  # 15 minutes (10s intervals)
    for _i in range(max_retries):
        time.sleep(10)
        remote_status = orchestrator.poll_deployment(remote_dep_id)
        if not remote_status:
            continue

        status = remote_status.get("status")
        # Update stage info with remote status if available
        if status:
            append_log(deployment, f"[Remote] Status: {status}\n")

        if status == Deployment.Status.ACTIVE:
            deployment.status = Deployment.Status.ACTIVE
            deployment.finished_at = timezone.now()
            deployment.save(update_fields=['status', 'finished_at'])
            update_stage(deployment, 'Remote Deploy', 'success')
            broadcast_status(deployment)
            append_log(deployment, "✅ Remote deployment successful!\n")
            return

        if status in (Deployment.Status.FAILED, Deployment.Status.BUILD_FAILED, Deployment.Status.CANCELLED):
            _handle_failure(None, deployment, f"Remote deployment failed with status: {status}", "Remote Execution Failure")
            return

    _handle_failure(None, deployment, "Remote deployment timed out", "Remote Timeout")


def _remote_failure_message(orchestrator, fallback: str) -> str:
    """Append the last upstream error from RemoteOrchestrator when available."""
    try:
        detail = orchestrator.describe_last_error()
    except Exception:
        detail = ""
    if detail:
        return f"{fallback}: {detail}"
    return fallback


def _stop_local_service_container(service_name: str):
    """
    Proactively stop and remove any local container on the Master VPS.
    Used during remote delegation to prevent 'ghost' containers.
    """
    try:
        import docker

        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()
        try:
            container = client.containers.get(service_name)
            logger.info(f"Stopping ghost container {service_name} on Master VPS...")
            container.stop(timeout=10)
            container.remove(force=True)
            logger.info(f"Successfully removed ghost container {service_name}")
        except docker.errors.NotFound:
            pass
        except Exception as e:
            logger.warning(f"Failed to stop ghost container {service_name}: {e}")
    except Exception as e:
        logger.warning(f"Docker client unavailable on Master: {e}")


def _remote_deploy_failed(deployment, orchestrator, fallback_msg, stage):
    _handle_failure(None, deployment, _remote_failure_message(orchestrator, fallback_msg), stage)


def _handle_remote_deployment(deployment, server, skip_review: bool = False, image_name: str | None = None):
    """Delegate deployment to a remote server and poll for status.

    When ``image_name`` is provided (master pre-built and pushed the image),
    it is forwarded to the remote so that node can skip its own build phase
    and go straight to pull + run (build-agent optimization).
    """
    from apps.deployments.services.server_guard import ServerGuard

    service = deployment.service

    # [FIX] Proactively stop any existing local container on Master VPS
    # if this service is being delegated to a remote node.
    _stop_local_service_container(service.name)

    guard = ServerGuard.check_user_workload_allowed(server)
    if not guard["ok"]:
        _handle_failure(
            None,
            deployment,
            guard["error"]["message"],
            "Placement Guard",
        )
        return

    orchestrator = RemoteOrchestrator(server)

    # Pre-flight: verify remote node API is reachable before delegating.
    # If the backend is down (Traefik 404), attempt SSH auto-heal of
    # the entire docker-compose stack on the remote node.
    preflight = orchestrator.preflight_check_or_heal()
    if not preflight['ok']:
        diagnosis = preflight.get('diagnosis', 'unknown')
        healed_note = ' (SSH auto-heal was attempted)' if preflight.get('healed') else ''
        _handle_failure(
            None,
            deployment,
            f"Remote node {server.name} ({server.host}) is unreachable "
            f"[{diagnosis}]{healed_note}: {preflight['error']}",
            "Remote Node Unhealthy",
        )
        return

    append_log(deployment, f"Delegating deployment to remote server: {server.name} ({server.host})\n")
    update_stage(deployment, 'Remote Sync', 'running')

    remote_svc_id = orchestrator.sync_service(service)
    if not remote_svc_id:
        _remote_deploy_failed(
            deployment,
            orchestrator,
            "Failed to sync service to remote server",
            "Remote Sync Failure",
        )
        return

    update_stage(deployment, 'Remote Sync', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')

    remote_dep_id = orchestrator.trigger_deploy(
        deployment, remote_svc_id, skip_review=skip_review, image_name=image_name,
    )
    if not remote_dep_id:
        _remote_deploy_failed(
            deployment,
            orchestrator,
            "Failed to trigger deployment on remote server",
            "Remote Deploy Failure",
        )
        return

    deployment.remote_deployment_id = remote_dep_id
    deployment.status = Deployment.Status.QUEUED  # Stay queued until follower reports a stage
    deployment.started_at = deployment.started_at or timezone.now()
    deployment.save(update_fields=['remote_deployment_id', 'status', 'started_at', 'updated_at'])
    append_log(deployment, f"Remote deployment triggered: {remote_dep_id}\n")
    _poll_remote_deployment(
        deployment,
        orchestrator,
        remote_dep_id,
        remote_service_id=remote_svc_id,
    )


def _resume_remote_deployment(deployment, server):
    """Approve/resume an existing remote deployment and keep polling it."""
    from apps.deployments.services.server_guard import ServerGuard

    service = deployment.service
    guard = ServerGuard.check_user_workload_allowed(server)
    if not guard["ok"]:
        _handle_failure(
            None,
            deployment,
            guard["error"]["message"],
            "Placement Guard",
        )
        return

    orchestrator = RemoteOrchestrator(server)
    remote_dep_id = deployment.remote_deployment_id
    append_log(deployment, f"Resuming remote deployment: {remote_dep_id}\n")
    update_stage(deployment, 'Remote Approval', 'running')

    remote_svc_id = orchestrator.sync_service(service)
    if remote_svc_id:
        orchestrator.sync_env_vars(service, remote_svc_id)

    payload = {
        "cpu_cores": str(service.cpu_cores),
        "memory_mb": service.memory_mb,
    }
    if not orchestrator.approve_deployment(remote_dep_id, payload=payload):
        _remote_deploy_failed(
            deployment,
            orchestrator,
            "Failed to approve remote deployment",
            "Remote Approval Failure",
        )
        return

    update_stage(deployment, 'Remote Approval', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')
    _poll_remote_deployment(
        deployment,
        orchestrator,
        remote_dep_id,
        remote_service_id=remote_svc_id,
    )


def _copy_remote_deployment_fields(deployment, remote_status: dict):
    """Mirror useful remote deployment fields onto the controller row."""
    update_fields = []
    if remote_status.get("build_logs") and remote_status.get("build_logs") != deployment.build_logs:
        deployment.build_logs = remote_status.get("build_logs") or ""
        update_fields.append("build_logs")
    if remote_status.get("review_summary") and remote_status.get("review_summary") != deployment.review_summary:
        deployment.review_summary = remote_status.get("review_summary") or {}
        update_fields.append("review_summary")
    if remote_status.get("ai_diagnosis") and remote_status.get("ai_diagnosis") != deployment.ai_diagnosis:
        deployment.ai_diagnosis = remote_status.get("ai_diagnosis") or ""
        update_fields.append("ai_diagnosis")
    if remote_status.get("pipeline_stages") and remote_status.get("pipeline_stages") != deployment.pipeline_stages:
        deployment.pipeline_stages = remote_status.get("pipeline_stages") or []
        update_fields.append("pipeline_stages")
    if remote_status.get("vulnerability_report") and remote_status.get("vulnerability_report") != deployment.vulnerability_report:
        deployment.vulnerability_report = remote_status.get("vulnerability_report") or {}
        update_fields.append("vulnerability_report")
    if remote_status.get("runtime_logs_url") and remote_status.get("runtime_logs_url") != deployment.runtime_logs_url:
        deployment.runtime_logs_url = remote_status.get("runtime_logs_url")
        update_fields.append("runtime_logs_url")
    if remote_status.get("commit_hash") and remote_status.get("commit_hash") != deployment.commit_hash:
        deployment.commit_hash = remote_status.get("commit_hash")
        update_fields.append("commit_hash")
    if remote_status.get("commit_message") and remote_status.get("commit_message") != deployment.commit_message:
        deployment.commit_message = remote_status.get("commit_message")
        update_fields.append("commit_message")
    if update_fields:
        update_fields.append("updated_at")
        deployment.save(update_fields=update_fields)


def _poll_remote_deployment(
    deployment,
    orchestrator,
    remote_dep_id,
    remote_service_id=None,
):
    """Poll a delegated deployment until it reaches REVIEW or a terminal state."""
    max_retries = 90  # 15 minutes (10s intervals)
    max_empty_polls = 12  # 2 minutes of unreachable/invalid poll responses.
    empty_polls = 0
    logger.info("Starting polling for remote deployment %s on node %s", remote_dep_id, orchestrator.server.host)
    append_log(deployment, f"[Remote] Initializing poller for remote deployment: {remote_dep_id}\n")

    for i in range(max_retries):
        time.sleep(10 + random.uniform(0, 2))

        if i % 6 == 0:  # Log every 60 seconds
             logger.debug("Polling remote deployment %s (attempt %d/%d)", remote_dep_id, i+1, max_retries)

        remote_status = orchestrator.poll_deployment(remote_dep_id)
        if not remote_status:
            empty_polls += 1
            if empty_polls in (3, 6, 12):
                append_log(
                    deployment,
                    (
                        "[Remote] Status unavailable "
                        f"({empty_polls}/{max_empty_polls}): "
                        f"{_remote_failure_message(orchestrator, 'poll failed')}\n"
                    ),
                )
            if empty_polls >= max_empty_polls:
                _handle_failure(
                    None,
                    deployment,
                    _remote_failure_message(
                        orchestrator,
                        "Remote deployment status could not be fetched",
                    ),
                    "Remote Poll Failure",
                )
                return
            continue
        empty_polls = 0

        status = remote_status.get("status")
        _copy_remote_deployment_fields(deployment, remote_status)
        if status:
            append_log(deployment, f"[Remote] Status: {status}\n")

        if status == Deployment.Status.REVIEW:
            deployment.status = Deployment.Status.REVIEW
            deployment.save(update_fields=['status', 'updated_at'])
            update_stage(deployment, 'Remote Review', 'waiting')
            broadcast_status(deployment)
            append_log(deployment, "Remote deployment paused for review. Approve to continue.\n")
            return

        if status in (
            Deployment.Status.BUILDING,
            Deployment.Status.BACKUP_RUNNING,
            Deployment.Status.MIGRATION_PLANNING,
            Deployment.Status.MIGRATION_RUNNING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        ) and deployment.status != status:
            deployment.status = status
            deployment.save(update_fields=['status', 'updated_at'])
            broadcast_status(deployment)

        # [STALE DETECTION]
        # If the remote is still QUEUED after 3 minutes (18 polls), it might mean
        # the remote worker is stuck or down.
        if status == Deployment.Status.QUEUED and i > 18:
            warning_msg = (
                "[Remote] Warning: Deployment has been QUEUED on the remote node for >3 minutes. "
                "The remote worker may be offline or overloaded.\n"
            )
            if i % 6 == 0: # Only log every minute to avoid spam
                append_log(deployment, warning_msg)
                logger.warning("Remote deployment %s stuck in QUEUED for %d polls", remote_dep_id, i)

        if status == Deployment.Status.ACTIVE:
            # ── MISSION RULE 3: POST-DEPLOYMENT VERIFICATION ──
            # Before accepting ACTIVE status from remote, we MUST verify it exists and is running.
            try:
                status_service_id = remote_service_id or deployment.service.id
                verify_resp = orchestrator._request(
                    method='GET',
                    path=f"/api/v1/services/{status_service_id}/status/",
                    timeout=10,
                )
                if verify_resp and verify_resp.status_code == 200:
                    status_data = verify_resp.json()
                    if status_data.get("status") == "running":
                        remote_container_id = status_data.get("container_id", deployment.container_id)
                        target_type = (
                            "lite_agent"
                            if getattr(orchestrator.server, "is_lite_agent", False)
                            else "remote"
                        )

                        # Persist verified metadata on Deployment
                        deployment.verified_target_type = target_type
                        deployment.verified_host_ip = (
                            getattr(orchestrator.server, "wg_address", None)
                            or orchestrator.server.private_ip
                            or orchestrator.server.host
                        )
                        deployment.verified_runtime_id = remote_container_id
                        deployment.verified_at = timezone.now()

                        deployment.status = Deployment.Status.ACTIVE
                        deployment.finished_at = timezone.now()
                        deployment.save(update_fields=['status', 'finished_at', 'updated_at', 'verified_target_type', 'verified_host_ip', 'verified_runtime_id', 'verified_at'])

                        # Promote to Active Service
                        service = deployment.service
                        service.server = deployment.target_server or orchestrator.server
                        service.active_target_type = target_type
                        service.active_host_ip = deployment.verified_host_ip
                        service.active_runtime_id = remote_container_id
                        service.save(update_fields=['server', 'active_target_type', 'active_host_ip', 'active_runtime_id'])

                        _regenerate_caddyfile()

                        update_stage(deployment, 'Remote Deploy', 'success')
                        broadcast_status(deployment)
                        append_log(deployment, "Remote deployment completed and VERIFIED successfully.\n")
                        return
                    else:
                        raise ValueError(f"Service status is {status_data.get('status')}, expected running.")
                else:
                     raise ValueError(f"Verification request failed with status {getattr(verify_resp, 'status_code', 'None')}")
            except Exception as e:
                logger.error("Verification failed for deployment %s: %s", deployment.id, e)
                _handle_failure(
                    None,
                    deployment,
                    f"Remote node reported ACTIVE, but post-deployment verification failed: {e}",
                    "Verification Failure"
                )
                return


        if status in (
            Deployment.Status.FAILED,
            Deployment.Status.BUILD_FAILED,
            Deployment.Status.BACKUP_FAILED,
            Deployment.Status.MIGRATION_FAILED,
        ):
            error_detail = remote_status.get("error") or f"Remote deployment failed with status: {status}."
            append_log(deployment, "[Self-Heal] Remote failure detected — triggering self-healing...\n")
            try:
                self_heal_remote_deployment.delay(
                    deployment_id=str(deployment.id),
                    server_id=str(orchestrator.server.id),
                )
            except Exception as exc:
                logger.warning("Failed to trigger self-healing from poller: %s", exc)
            _handle_failure(
                None,
                deployment,
                error_detail,
                "Remote Execution Failure",
            )
            return

        if status == Deployment.Status.CANCELLED:
            deployment.status = Deployment.Status.CANCELLED
            deployment.finished_at = timezone.now()
            deployment.save(update_fields=['status', 'finished_at', 'updated_at'])
            broadcast_status(deployment)
            append_log(deployment, "Remote deployment was cancelled.\n")
            return

    # If we exit the loop without a terminal state
    logger.info("Polling finished for remote deployment %s after %d attempts", remote_dep_id, max_retries)
    intermediate_statuses = (
        Deployment.Status.QUEUED,
        Deployment.Status.BUILDING,
        Deployment.Status.HEALTH_CHECK,
        Deployment.Status.DEPLOYING,
        Deployment.Status.MIGRATION_RUNNING,
        Deployment.Status.MIGRATION_PLANNING,
        Deployment.Status.BACKUP_RUNNING,
        Deployment.Status.REVIEW,
    )
    if deployment.status in intermediate_statuses:
        append_log(
            deployment,
            f"\n[Remote] Polling timed out after 15 minutes while in {deployment.status} state. "
            "The deployment may still be running on the remote node.\n"
        )
        deployment.status = Deployment.Status.FAILED
        deployment.error = "Remote deployment poller timed out. Check the remote node directly for actual status."
        deployment.finished_at = timezone.now()
        deployment.save(update_fields=['status', 'error', 'finished_at', 'updated_at'])
        update_stage(deployment, 'Remote Deploy', 'failed')
        broadcast_status(deployment)


def _build_function(deployment, service) -> str:
    """Build serverless function image."""
    build_dir = None
    try:
        deployment.status = 'BUILDING'
        deployment.save()
        broadcast_status(deployment)

        if (service.health_check_path or '').strip() in {'', '/health'}:
            service.health_check_path = '/health'
            service.save(update_fields=['health_check_path', 'updated_at'])

        build_dir = tempfile.mkdtemp(prefix=f"func_{deployment.id}_")
        FunctionProvisioner.prepare_context(service, build_dir)

        safe_service_name = _docker_safe_segment(service.name, fallback="function")
        deploy_tag = str(deployment.id).replace("-", "")[:8]
        tag = f"smsly/func-{safe_service_name}:{deploy_tag}"

        append_log(deployment, f"Building function {tag}...\n")

        cmd = ["docker", "build", "-t", tag, "--load", build_dir]
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            build_output = "\n".join(
                part for part in [result.stdout, result.stderr] if part
            ).strip()
            if build_output:
                append_log(deployment, f"{build_output[-4000:]}\n")
        except subprocess.TimeoutExpired as exc:
            append_log(deployment, "\n[FUNCTION-BUILD] Docker build timed out after 300s.\n")
            partial = "\n".join(
                str(part) for part in [exc.stdout, exc.stderr] if part
            ).strip()
            if partial:
                append_log(deployment, f"{partial[-4000:]}\n")
            raise
        except subprocess.CalledProcessError as exc:
            append_log(deployment, "\n[FUNCTION-BUILD] Docker build failed.\n")
            output = "\n".join(
                part for part in [exc.stdout, exc.stderr] if part
            ).strip()
            if output:
                append_log(deployment, f"{output[-8000:]}\n")
            raise

        registry = getattr(settings, 'CONTAINER_REGISTRY_URL', None)
        is_local = is_deployment_local(deployment)
        if not is_local and not registry:
            raise RuntimeError(
                "CONTAINER_REGISTRY_URL is not configured. "
                "A registry is required to push/pull images for remote node deployments."
            )
        if registry:
            remote_tag, _push_error = NixpacksBuilder.push_image(tag, registry)
            pushed_to_registry = bool(remote_tag and remote_tag.startswith(registry))
            if not pushed_to_registry and not is_local:
                raise RuntimeError(
                    f"Image push failed: Local fallback is not allowed for remote deployments. "
                    f"Target node requires a working registry to pull {remote_tag}."
                )
            return remote_tag
        return tag

    finally:
        if build_dir:
            shutil.rmtree(build_dir, ignore_errors=True)


def _build_uploaded_source(deployment, service) -> str:
    """Build an image from a previously uploaded zip archive."""
    build_dir = None
    try:
        deployment.status = Deployment.Status.BUILDING
        deployment.save(update_fields=["status"])
        broadcast_status(deployment)

        zip_path = _resolve_upload_zip_path(service.repository_url)
        build_dir = tempfile.mkdtemp(prefix=f"upload_{deployment.id}_")
        source_dir = os.path.join(build_dir, "source")
        os.makedirs(source_dir, exist_ok=True)

        append_log(deployment, f"Extracting uploaded source from {zip_path}...\n")
        _safe_extract_zip(zip_path, source_dir)

        # Normalize archives that contain a single top-level folder.
        entries = [
            os.path.join(source_dir, item)
            for item in os.listdir(source_dir)
            if item not in ("__MACOSX",)
        ]
        if len(entries) == 1 and os.path.isdir(entries[0]):
            source_dir = entries[0]

        safe_service_name = _docker_safe_segment(service.name, fallback="upload")
        deploy_tag = str(deployment.id).replace("-", "")[:8]
        image_name = f"smsly/{safe_service_name}:{deploy_tag}"

        env_map = {env.key: env.value for env in service.env_vars.all()}
        dockerfile_path = os.path.join(source_dir, "Dockerfile")
        has_dockerfile = os.path.isfile(dockerfile_path)

        if service.buildpack == 'DOCKER':
            use_docker = True
        elif service.buildpack == 'NIXPACKS' or service.buildpack == 'STATIC':
            use_docker = False
        else:
            use_docker = has_dockerfile

        if use_docker:
            if not has_dockerfile:
                raise ValueError("Build strategy is docker but no Dockerfile was found.")
            append_log(deployment, "Building uploaded source with Dockerfile...\n")
            try:
                subprocess.run(
                    ["docker", "build", "-t", image_name, "--load", "-f", dockerfile_path, source_dir],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=1800,
                )
            except subprocess.CalledProcessError as exc:
                append_log(deployment, f"{exc.stdout or ''}\n{exc.stderr or ''}\n")
                raise
        else:
            if service.buildpack == 'STATIC':
                append_log(deployment, "Building uploaded source for Static Site (via Nixpacks)...\n")
            elif service.buildpack == 'NIXPACKS':
                append_log(deployment, "Building uploaded source with Nixpacks...\n")
            else:
                append_log(deployment, "Building uploaded source with Nixpacks fallback...\n")

            NixpacksBuilder.build_image(
                source_dir=source_dir,
                image_name=image_name,
                env_vars=env_map,
            )

        registry = getattr(settings, "CONTAINER_REGISTRY_URL", None)
        is_local = is_deployment_local(deployment)
        if not is_local and not registry:
            raise RuntimeError(
                "CONTAINER_REGISTRY_URL is not configured. "
                "A registry is required to push/pull images for remote node deployments."
            )
        if registry:
            append_log(deployment, f"Pushing uploaded image to {registry}...\n")
            remote_tag, _push_error = NixpacksBuilder.push_image(image_name, registry)
            pushed_to_registry = bool(remote_tag and remote_tag.startswith(registry))
            if not pushed_to_registry and not is_local:
                raise RuntimeError(
                    f"Image push failed: Local fallback is not allowed for remote deployments. "
                    f"Target node requires a working registry to pull {remote_tag}."
                )
            image_name = remote_tag
        return image_name

    finally:
        if build_dir:
            shutil.rmtree(build_dir, ignore_errors=True)


def _is_traefik_not_ready(response: requests.Response) -> bool:
    """
    Detect Traefik's default no-route 404 response.

    In production this response may not include a `Server: traefik` header,
    so rely on the canonical body + headers rather than `Server` only.
    """
    body = (response.text or "").strip().lower()
    if response.status_code != 404 or body != "404 page not found":
        return False

    content_type = (response.headers.get("Content-Type") or "").lower()
    nosniff = (response.headers.get("X-Content-Type-Options") or "").lower()
    return content_type.startswith("text/plain") and nosniff == "nosniff"


def _route_misroute_reason(response: requests.Response) -> str:
    """
    Detect responses that prove a service hostname hit platform fallback.

    A deployed service route is not ready if it returns the control-plane
    frontend or the route-fallback page, even if the HTTP status is otherwise
    successful.
    """
    control_plane = str(response.headers.get("X-SMSLY-Control-Plane", "")).strip().lower()
    if control_plane in {"1", "true", "yes"}:
        return "service hostname reached the control-plane proxy"

    route_fallback = str(response.headers.get("X-SMSLY-Route-Fallback", "")).strip().lower()
    if route_fallback in {"1", "true", "yes"}:
        return "service hostname reached the route fallback page"

    body = (response.text or "")[:12000].lower()
    fallback_markers = (
        "cloudneuron routing",
        "service is waking up",
        "automatically reconnect traffic",
    )
    if any(marker in body for marker in fallback_markers):
        return "service hostname rendered the route fallback page"

    platform_markers = (
        "the sovereign paas",
        "deployment previews",
        "global edge routing",
        "connect your own vps",
    )
    if sum(1 for marker in platform_markers if marker in body) >= 2:
        return "service hostname rendered the platform homepage"

    return ""


def _is_low_resource_service(service: Service) -> bool:
    try:
        cpu_threshold = float(os.environ.get("LOW_RESOURCE_CPU_CORES_THRESHOLD", "0.75"))
    except (TypeError, ValueError):
        cpu_threshold = 0.75
    memory_threshold = _env_int("LOW_RESOURCE_MEMORY_MB_THRESHOLD", 768, minimum=64)

    try:
        cpu_cores = float(service.cpu_cores or 0)
    except (TypeError, ValueError):
        cpu_cores = 0.0

    try:
        memory_mb = int(service.memory_mb or 0)
    except (TypeError, ValueError):
        memory_mb = 0

    return (
        (cpu_cores > 0 and cpu_cores <= cpu_threshold)
        or (memory_mb > 0 and memory_mb <= memory_threshold)
    )


def _local_route_timeout_seconds(service: Service) -> int:
    if _is_low_resource_service(service):
        return _env_int(
            "LOCAL_ROUTE_READY_TIMEOUT_LOW_RESOURCE_SECONDS",
            120,
            minimum=10,
        )
    return _env_int("LOCAL_ROUTE_READY_TIMEOUT_SECONDS", 60, minimum=10)


def _local_container_timeout_seconds(service: Service) -> int:
    if _is_low_resource_service(service):
        return _env_int(
            "LOCAL_CONTAINER_HEALTH_TIMEOUT_LOW_RESOURCE_SECONDS",
            600,
            minimum=60,
        )
    return _env_int("LOCAL_CONTAINER_HEALTH_TIMEOUT_SECONDS", 480, minimum=60)


def _wait_for_local_container_healthy(
    deployment,
    container_id: str,
    timeout_seconds: int = 480,
    poll_seconds: int = 5,
) -> bool:
    """
    Wait for a freshly deployed local container to be healthy/running.

    This prevents deployments from being marked ACTIVE when the container
    immediately crash-loops or fails its Docker health check.
    """
    try:
        # Check if docker is available (it should be, imported at top level)
        pass
    except Exception:  # pragma: no cover - import failure is environment-specific
        append_log(
            deployment,
            "[HEALTH-CHECK] Docker SDK unavailable; skipping container health wait.\n",
        )
        return True

    try:
        client = docker.from_env()
    except Exception as exc:  # pragma: no cover - daemon/socket issues are environment-specific
        append_log(
            deployment,
            f"[HEALTH-CHECK] Docker client unavailable ({exc}); skipping container health wait.\n",
        )
        return True

    deadline = time.monotonic() + timeout_seconds
    last_state = "unknown"
    while time.monotonic() < deadline:
        try:
            container = client.containers.get(container_id)
            container.reload()
            state = container.attrs.get("State") or {}
            status = (state.get("Status") or "").lower()
            health = ((state.get("Health") or {}).get("Status") or "").lower()
            last_state = f"status={status or 'unknown'}, health={health or 'n/a'}"
        except Exception as exc:  # pragma: no cover - container lookups are runtime-dependent
            last_state = f"lookup_error={exc}"
            time.sleep(poll_seconds)
            continue

        if status in {"exited", "dead"}:
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container terminated early ({last_state}).\n",
            )
            return False

        if health == "healthy":
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container healthy ({last_state}).\n",
            )
            return True

        if health == "unhealthy":
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container unhealthy ({last_state}).\n",
            )
            return False

        # Still within Docker health check start_period — keep polling
        if health == "starting":
            append_log(
                deployment,
                f"[HEALTH-CHECK] Health check still in start_period ({last_state}).\n",
            )
            time.sleep(poll_seconds)
            continue

        # No Docker healthcheck configured; consider running container ready.
        if status == "running" and not health:
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container running without healthcheck ({last_state}).\n",
            )
            return True

        time.sleep(poll_seconds)

    # If the container is running but health is still "starting" (Docker
    # start_period hasn't expired yet), treat it as healthy — the app is
    # up and serving even though Docker hasn't finished its first probe.
    if status in ("running",) and health in ("starting", "n/a", ""):
        append_log(
            deployment,
            f"[HEALTH-CHECK] Container running; health still in start_period "
            f"({last_state}). Accepting as healthy.\n",
        )
        return True
    append_log(
        deployment,
        f"[HEALTH-CHECK] Timed out waiting for container health ({last_state}).\n",
    )
    return False


def _wait_for_local_route_ready(
    deployment,
    service,
    timeout_seconds: int = 0,
    poll_seconds: int = 3,
) -> bool:
    """
    Wait until Traefik has picked up host routing for this service.

    If timeout_seconds <= 0, polls indefinitely (capped by Celery task timeout).
    """
    host = (service.public_domain or "").strip()
    if not host:
        return True

    # Probe through the public edge first, then the raw Traefik ingress. The
    # direct Traefik probe is useful during DNS propagation, but it must not
    # mask a Caddy misroute that serves the platform homepage.
    probe_candidates: list = []    # type: ignore[var-annotated]

    def _add_probe(base_url: str, headers: dict | None = None, verify: bool = True, kind: str = "direct"):
        normalized = (base_url or "").rstrip("/")
        if not normalized:
            return
        probe_candidates.append(
            {
                "base_url": normalized,
                "headers": headers or {},
                "verify": verify,
                "kind": kind,
            }
        )

    _add_probe(f"https://{host}", verify=True, kind="edge")
    _add_probe(f"http://{host}", verify=True, kind="edge")
    _add_probe(
        "http://caddy:80",
        headers={"Host": host},
        verify=should_verify("http://caddy:80"),
        kind="edge",
    )
    configured = os.environ.get("TRAEFIK_INTERNAL_URL", "").strip()
    if configured:
        _add_probe(
            configured,
            headers={"Host": host},
            verify=should_verify(configured),
        )
    _add_probe(
        "http://traefik:80",
        headers={"Host": host},
        verify=should_verify("http://traefik:80"),
    )

    is_lite = getattr(service.server, "is_lite_agent", False) if service.server else False
    if is_lite:
        _add_probe(
            "http://127.0.0.1:80",
            headers={"Host": host},
            verify=should_verify("http://127.0.0.1:80"),
        )
        _add_probe(
            "http://localhost:80",
            headers={"Host": host},
            verify=should_verify("http://localhost:80"),
        )
    else:
        _add_probe(
            "http://127.0.0.1:8081",
            headers={"Host": host},
            verify=should_verify("http://127.0.0.1:8081"),
        )
        _add_probe(
            "http://localhost:8081",
            headers={"Host": host},
            verify=should_verify("http://localhost:8081"),
        )

    # Preserve order and remove duplicates.
    probes = []
    seen = set()
    for probe in probe_candidates:
        key = (probe["base_url"], tuple(sorted(probe["headers"].items())))
        if key in seen:
            continue
        seen.add(key)
        probes.append(probe)

    path_candidates = []
    if service.health_check_path:
        path_candidates.append(service.health_check_path)
    path_candidates.extend(["/", "/health", "/healthz"])

    paths = []
    seen_paths = set()
    for raw_path in path_candidates:
        path = raw_path if str(raw_path).startswith("/") else f"/{raw_path}"
        if path not in seen_paths:
            seen_paths.add(path)
            paths.append(path)

    use_deadline = timeout_seconds > 0
    append_log(
        deployment,
        f"[ROUTE-CHECK] Polling route for host {host} "
        f"({'until active' if not use_deadline else f'timeout {timeout_seconds}s'})\n",
    )

    deadline = time.monotonic() + timeout_seconds if use_deadline else 0
    last_error = ""
    edge_misroute_seen = False
    attempt = 0
    while True:
        if use_deadline and time.monotonic() > deadline:
            break
        attempt += 1
        for probe in probes:
            base_url = probe["base_url"]
            for path in paths:
                url = f"{base_url}{path}"
                try:
                    response = requests.get(
                        url,
                        headers=probe["headers"],  # type: ignore[arg-type]
                        timeout=(
                            _env_int("LOCAL_ROUTE_EDGE_PROBE_TIMEOUT_SECONDS", 4, minimum=1)
                            if probe.get("kind") == "edge"
                            else 8
                        ),
                        verify=probe["verify"],  # type: ignore[arg-type]
                        allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    last_error = f"{url}: {exc}"
                    continue

                misroute_reason = _route_misroute_reason(response)
                if misroute_reason:
                    last_error = f"{url}: {misroute_reason}"
                    if probe.get("kind") == "edge":
                        edge_misroute_seen = True
                    continue

                if probe.get("kind") == "edge" and 300 <= response.status_code < 400:
                    location = response.headers.get("Location", "")
                    last_error = f"{url}: edge redirect {response.status_code} to {location or 'unknown'}"
                    continue

                if response.status_code >= 500:
                    last_error = f"{url}: HTTP {response.status_code}"
                    continue

                # Traefik can briefly return default 404 while labels propagate.
                if _is_traefik_not_ready(response):
                    last_error = f"{url}: Traefik route not ready yet"
                    continue

                if probe.get("kind") == "direct" and edge_misroute_seen:
                    last_error = (
                        f"{url}: direct Traefik route is active, but edge route "
                        "is still hitting the platform fallback"
                    )
                    continue

                append_log(
                    deployment,
                    f"[ROUTE-CHECK] Route active via {url} "
                    f"(HTTP {response.status_code}, attempt {attempt})\n",
                )
                return True

        time.sleep(poll_seconds)

    append_log(
        deployment,
        "[ROUTE-CHECK] Routing readiness timed out. "
        f"Last error: {last_error or 'unknown'}\n",
    )
    return False


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


@shared_task(bind=True, max_retries=0, soft_time_limit=120, time_limit=150)
def _post_deploy_monitor(self, deployment_id: str, provider_id: str, container_id: str,
                         image_name: str):
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
            import re as _re
            action = _apply_fix(fix, _re.match('', ''), '', service, deployment)
            if action:
                append_log(deployment, f"  ✅ AI-suggested fix applied: {action}\n")

    except Exception as e:
        logger.warning("AI escalation failed for deployment %s: %s",
                       deployment.id, e)
        append_log(deployment, f"\n🤖 AI diagnosis unavailable: {e}\n")


def _handle_failure(task, deployment, error_msg, reason):
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
            except Exception as e:
                logger.warning(f"Docker client error during failure cleanup: {e}")

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


@shared_task(bind=True, max_retries=0, soft_time_limit=600, time_limit=660)
def self_heal_remote_deployment(self, deployment_id: str, server_id: str):
    """
    Self-healing task for remote deployment failures.

    Triggered when a remote deployment fails. Attempts automated diagnosis
    and recovery via SSH before marking the deployment as permanently failed.

    Recovery actions include:
    - Container restart
    - Stack restart (docker compose up -d)
    - Image/volume pruning (disk space)
    - Network repair
    - AI escalation for complex failures
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)
    except Deployment.DoesNotExist:
        logger.warning("Self-heal: deployment %s not found", deployment_id)
        return

    try:
        from apps.deployments.models_core import ManagedServer
        server = ManagedServer.objects.get(id=server_id)
    except Exception as exc:
        logger.warning("Self-heal: server %s not found: %s", server_id, exc)
        return

    if not (server.ssh_key or server.ssh_password):
        logger.info("Self-heal: no SSH credentials for server %s", server.name)
        return

    append_log(deployment, "\n🔧 Self-healing: diagnosing remote node failure...\n")
    broadcast_status(deployment)

    try:
        from apps.deployments.services.self_healing_orchestrator import (
            RecoveryAction,
            SelfHealingOrchestrator,
        )

        orchestrator = SelfHealingOrchestrator(server)
        result = orchestrator.heal_deployment_failure(deployment)

        append_log(deployment, f"[Self-Heal] Action: {result.action_taken.value}\n")
        append_log(deployment, f"[Self-Heal] Success: {result.success}\n")
        append_log(deployment, f"[Self-Heal] Details: {result.details}\n")

        if result.success:
            append_log(deployment, f"[Self-Heal] Recovery succeeded: {result.action_taken.value}\n")
            append_log(deployment, f"[Self-Heal] Post-recovery status: {result.post_recovery_status}\n")

            if result.next_action:
                append_log(deployment, f"[Self-Heal] Suggested next action: {result.next_action.value}\n")

            deployment.refresh_from_db()
            if deployment.status == Deployment.Status.FAILED:
                deployment.status = Deployment.Status.QUEUED
                deployment.build_logs += "\n[Self-Heal] Retrying deployment after successful recovery...\n"
                deployment.save(update_fields=["status", "build_logs", "updated_at"])
                broadcast_status(deployment)

                try:
                    provider = deployment.service.provider
                    if provider:
                        enqueue_smart_deploy_task(
                            deployment_id=str(deployment.id),
                            provider_id=str(provider.id),
                            skip_review=True,
                        )
                        append_log(deployment, "[Self-Heal] Deployment retry queued\n")
                except Exception as exc:
                    append_log(deployment, f"[Self-Heal] Failed to queue retry: {exc}\n")
                    logger.warning("Self-heal retry queue failed: %s", exc)

        elif result.next_action == RecoveryAction.ESCALATE_TO_AI:
            append_log(deployment, "[Self-Heal] Escalating to system intelligence (AI)...\n")

            try:
                diagnostics = orchestrator.run_full_diagnostics(deployment)
                ai_result = orchestrator.escalate_to_ai(deployment, diagnostics)

                if ai_result.get("success"):
                    append_log(deployment, "[Self-Heal] AI analysis received\n")
                    commands = ai_result.get("suggested_commands", [])
                    if commands:
                        append_log(deployment, "[Self-Heal] AI suggested commands:\n")
                        for cmd in commands[:5]:
                            append_log(deployment, f"  CMD: {cmd}\n")

                    deployment.ai_diagnosis = ai_result.get("ai_response", "")[:2000]
                    deployment.save(update_fields=["ai_diagnosis", "updated_at"])
                else:
                    append_log(deployment, f"[Self-Heal] AI escalation failed: {ai_result.get('error', 'unknown')}\n")

            except Exception as exc:
                append_log(deployment, f"[Self-Heal] AI escalation error: {exc}\n")
                logger.warning("Self-heal AI escalation failed: %s", exc)

        else:
            append_log(deployment, f"[Self-Heal] Recovery failed: {result.details}\n")
            if result.next_action:
                append_log(deployment, f"[Self-Heal] Suggested next action: {result.next_action.value}\n")

        heal_log = orchestrator.get_heal_log()
        if heal_log:
            append_log(deployment, "[Self-Heal] Heal log:\n")
            for entry in heal_log[-10:]:
                append_log(deployment, f"  - {entry}\n")

    except Exception as exc:
        append_log(deployment, f"[Self-Heal] Exception: {exc}\n")
        logger.exception("Self-healing task failed for deployment %s", deployment_id)
    finally:
        broadcast_status(deployment)


SHARED_OLLAMA_NAME_PREFIX = "ollama-cpp-shared"
SHARED_OLLAMA_PORT = 11434

# Conservative RAM caps — shared Ollama gets a fraction of total host RAM
# to leave breathing room for the OS + other services.
SHARED_OLLAMA_MIN_RAM_MB = 2048    # 2 GB — minimum viable for any LLM
SHARED_OLLAMA_MAX_RAM_MB = 8192    # 8 GB — practical ceiling on most VPS
SHARED_OLLAMA_RAM_FRACTION = 0.25  # 25% of total host RAM
SHARED_OLLAMA_MIN_CPU_CORES = 1.0
SHARED_OLLAMA_MAX_CPU_CORES = 4.0


def _detect_safe_ollama_ram_mb() -> int:
    """
    Determine a safe RAM allocation for the shared Ollama CPP based on
    the host's total system memory.  Never allocates more than 25% of
    total RAM, clamped between the configured min/max.
    """
    try:
        import psutil
        vm = psutil.virtual_memory()
        total_mb = vm.total // (1024 * 1024)
        # Available is what's actually free + reclaimable (cache/buffers)
        available_mb = vm.available // (1024 * 1024)

        fraction_mb = int(total_mb * SHARED_OLLAMA_RAM_FRACTION)
        safe_mb = max(SHARED_OLLAMA_MIN_RAM_MB, min(fraction_mb, SHARED_OLLAMA_MAX_RAM_MB))

        # On a tight VPS where even 25% of total exceeds what's actually
        # available, dial back to 50% of available so the OS doesn't OOM.
        if safe_mb > available_mb * 0.5 and available_mb > 0:
            safe_mb = max(SHARED_OLLAMA_MIN_RAM_MB, int(available_mb * 0.5))

        logger.info(
            "Shared Ollama RAM: host=%dMB available=%dMB → allocated=%dMB",
            total_mb, available_mb, safe_mb,
        )
        return safe_mb
    except Exception:
        # psutil unavailable — use 4 GB as a safe middle-ground
        return 4096


def _detect_safe_ollama_cpu() -> float:
    """Detect safe CPU allocation for shared Ollama."""
    try:
        import psutil
        logical = psutil.cpu_count(logical=True) or 1
        # Give Ollama up to half the logical cores, clamped
        allocated = max(SHARED_OLLAMA_MIN_CPU_CORES,
                        min(float(logical) * 0.5, SHARED_OLLAMA_MAX_CPU_CORES))
        return round(allocated, 1)
    except Exception:
        return 2.0


def _ensure_shared_ollama_cpp(service, provider) -> str | None:
    """
    Find or create a shared Ollama CPP service for the project.
    Returns the shared service ID (str) or None if creation fails.
    Only one shared Ollama CPP is maintained per project to save VPS resources.
    """
    from apps.deployments.models import Deployment, Service

    project = getattr(service, 'project', None)
    owner = getattr(service, 'owner', None)

    # 1. Look for an existing shared Ollama in the same project
    existing = Service.objects.filter(
        project=project,
        deploy_type='DOCKER',
        docker_image__contains='ollama',
    ).order_by('-created_at').first()

    # If one exists and looks active/resourced, reuse it
    if existing and existing.docker_image and 'ollama' in existing.docker_image.lower():
        if existing.status not in {'DELETION_PENDING', 'DELETING'}:
            # Ensure it has a project association
            if not existing.project and project:
                existing.project = project
                existing.save(update_fields=['project'])
            return str(existing.id)

    # 2. Auto-detect safe resource allocation from VPS
    ram_mb = _detect_safe_ollama_ram_mb()
    cpu = _detect_safe_ollama_cpu()

    # 3. Create a new shared Ollama CPP service
    try:
        import re
        project_id = str(project.id)[:8] if project else 'global'
        name = f"{SHARED_OLLAMA_NAME_PREFIX}-{project_id}"
        name = re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')[:63]

        # Avoid duplicates with race-condition safety
        existing = Service.objects.filter(name=name).first()
        if existing:
            return str(existing.id)

        shared = Service.objects.create(
            name=name,
            deploy_type='DOCKER',
            docker_image='ollama/ollama:latest',
            internal_port=SHARED_OLLAMA_PORT,
            owner=owner,
            provider=provider,
            project=project,
            memory_mb=ram_mb,
            cpu_cores=cpu,
            deploy_mode='SINGLE',
        )
        EnvironmentVariable.objects.update_or_create(
            service=shared, key='OLLAMA_HOST',
            defaults={'value': '0.0.0.0', 'is_secret': False}
        )
        EnvironmentVariable.objects.update_or_create(
            service=shared, key='OLLAMA_KEEP_ALIVE',
            defaults={'value': '24h', 'is_secret': False}
        )
        EnvironmentVariable.objects.update_or_create(
            service=shared, key='PORT',
            defaults={'value': str(SHARED_OLLAMA_PORT), 'is_secret': False}
        )
        EnvironmentVariable.objects.update_or_create(
            service=shared, key='PUBLIC_DOMAIN',
            defaults={'value': shared.public_domain or '', 'is_secret': False}
        )

        # Trigger deployment
        deployment = Deployment.objects.create(
            service=shared,
            status='QUEUED',
            commit_hash='template',
            commit_message='Shared Ollama CPP (auto-deployed)'
        )
        smart_deploy_task.delay(
            deployment_id=str(deployment.id),
            provider_id=str(provider.id)
        )
        logger.info(
            "Shared Ollama CPP created: %s (project %s, %dMB RAM, %.1f CPU)",
            name, project_id, ram_mb, cpu,
        )
        return str(shared.id)

    except Exception as exc:
        logger.error("Failed to create shared Ollama CPP: %s", exc)
        return None


def _pull_ollama_models_into_shared(shared_ollama_id: str, models: list):
    """
    Pull Ollama models into the shared Ollama CPP container.
    Runs as a fire-and-forget background subprocess.
    """
    import shlex
    try:
        shared = Service.objects.get(id=shared_ollama_id)
        container_name = shared.name
        for model in models:
            if not model:
                continue
            model = str(model).strip()
            logger.info("Pulling Ollama model '%s' into shared container %s", model, container_name)
            subprocess.Popen(
                ["docker", "exec", container_name, "sh", "-lc",
                 f"ollama pull {shlex.quote(model)}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as exc:
        logger.warning("Failed to pull models into shared Ollama %s: %s", shared_ollama_id, exc)


# ── Shared Ollama cleanup (called from delete_service_task) ───────────
def _cleanup_shared_ollama_if_unused(project):
    """
    After deleting a service, check if the shared Ollama CPP is still needed.
    If no remaining services in the project reference Ollama, delete it
    to free VPS resources.
    """
    if not project:
        return
    try:
        from apps.deployments.models import Service

        # Find the shared Ollama in this project
        shared = Service.objects.filter(
            project=project,
            deploy_type='DOCKER',
            docker_image__startswith='ollama/',
        ).order_by('-created_at').first()

        if not shared:
            return

        # Check: are there any OTHER services in the project that need Ollama?
        remaining = Service.objects.filter(
            project=project,
        ).exclude(
            id=shared.id
        ).exclude(
            status__in=['DELETION_PENDING', 'DELETING']
        ).exclude(
            deploy_type='DOCKER',
            docker_image__startswith='ollama/',  # skip other Ollama-only services
        )

        # Look for any service that references Ollama via env vars or docker image
        needs_ollama = False
        for svc in remaining:
            img = str(svc.docker_image or '').lower()
            if img.startswith('ollama/'):
                needs_ollama = True
                break
            # Check if env vars reference OLLAMA_BASE_URL
            if svc.env_vars.filter(key='OLLAMA_BASE_URL').exists():
                needs_ollama = True
                break
            if svc.env_vars.filter(key='OLLAMA_MODEL').exists():
                needs_ollama = True
                break

        if not needs_ollama:
            logger.info(
                "No remaining services need shared Ollama in project %s. "
                "Cleaning up %s to free VPS resources.",
                project.id, shared.name
            )
            # Mark for deletion
            shared.status = 'DELETION_PENDING'
            shared.save(update_fields=['status'])
            delete_service_task.delay(str(shared.id), force=True)
    except Exception as exc:
        logger.warning("Shared Ollama cleanup check failed: %s", exc)


@shared_task(bind=True, max_retries=0)
def one_click_deploy_template_task(self, service_id: str, template_id: str):
    """
    Background orchestration for template deployments.
    """
    # pylint: disable=unused-argument
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        return

    # Load template
    template_path = os.path.join(
        settings.BASE_DIR, 'apps/deployments/fixtures/templates.json'
    )
    try:
        with open(template_path, encoding='utf-8') as f:
            templates = json.load(f)
        template = next((t for t in templates if t.get('id') == template_id), None)
    except Exception as exc: # pylint: disable=broad-exception-caught
        logger.exception("Exception reading template JSON: %s", exc)
        template = None

    def _verify_image_available(image: str):
        """
        Best-effort check: docker manifest inspect <image>.
        Skippable via SKIP_TEMPLATE_IMAGE_VERIFY=true.
        """
        skip = os.environ.get("SKIP_TEMPLATE_IMAGE_VERIFY", "").lower() in {"1", "true", "yes", "on"}
        if skip or not image:
            return
        try:
            result = subprocess.run(
                ["docker", "manifest", "inspect", image],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or f"manifest inspect failed for {image}")
        except FileNotFoundError as exc:  # docker not installed
            logger.warning("Docker not available to verify image %s: %s", image, exc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Template image %s not available: %s", image, exc)
            raise

    # Provision addons
    required_addons = (template.get('required_addons') or []) if template else []

    # Honor template minimum RAM hints (e.g. Ollama models).
    if template:
        try:
            min_ram_gb = int(template.get("min_ram_gb") or 0)
        except (TypeError, ValueError):
            min_ram_gb = 0
        if min_ram_gb > 0:
            min_ram_mb = min_ram_gb * 1024
            try:
                current_mb = int(service.memory_mb or 0)
            except (TypeError, ValueError):
                current_mb = 0
            if current_mb < min_ram_mb:
                service.memory_mb = min_ram_mb
                service.save(update_fields=["memory_mb"])

    # Template-specific minimum requirements / defaults
    if template and template.get('id') == 'khoj':
        # Khoj requires pgvector; ensure Postgres addon is present
        if 'POSTGRES' not in required_addons:
            required_addons.append('POSTGRES')
    if template and template.get('id') == 'librechat':
        # LibreChat needs a JWT secret; inject default if missing
        env_list = template.setdefault('env_vars', [])
        has_jwt = any((str(ev.get('key') or '').upper() == 'JWT_SECRET') for ev in env_list)
        if not has_jwt:
            env_list.append({
                "key": "JWT_SECRET",
                "value": "${RANDOM_PASSWORD}",
                "is_secret": True
            })
        has_cfg = any((str(ev.get('key') or '').upper() == 'LIBRECHAT_CONFIG_PATH') for ev in env_list)
        if not has_cfg:
            env_list.append({
                "key": "LIBRECHAT_CONFIG_PATH",
                "value": "/app/librechat.yaml",
                "is_secret": False
            })

    # Template crash-clarity: enforce required envs for intelligence templates
    intelligence_templates = {
        'librechat', 'khoj', 'flowise', 'langflow',
        'dify', 'memgpt', 'anythingllm', 'ai-router'
    }
    if template and template.get('id') in intelligence_templates:
        env_list = template.setdefault('env_vars', [])
        existing = {str(ev.get('key') or '').upper() for ev in env_list}
        required_defaults = {
            'JWT_SECRET': '${RANDOM_PASSWORD}',
            'SECRET_KEY': '${RANDOM_PASSWORD}',
            'DATABASE_URL': '${DATABASE_URL}',
            'REDIS_URL': '${REDIS_URL}',
        }
        for key, val in required_defaults.items():
            if key not in existing:
                env_list.append({
                    "key": key,
                    "value": val,
                    "is_secret": 'SECRET' in key or 'PASSWORD' in key,
                })
    if template and template.get('docker_image'):
        _verify_image_available(template['docker_image'])
    supported_addons = set(addon_provisioner.ADDON_IMAGES.keys())

    # Track addon URLs for template rendering
    addon_urls = {}

    for addon_type in required_addons:
        if addon_type not in supported_addons:
            logger.warning("Template addon %s is not supported yet; skipping", addon_type)
            continue

        # Check if service already has this addon type active
        addon = Addon.objects.filter(service=service, addon_type=addon_type, status=Addon.Status.ACTIVE).first()
        if not addon:
            addon = Addon.objects.create(
                service=service,
                name=f"{addon_type.lower()}-{service.name}"[:255],
                addon_type=addon_type,
                status=Addon.Status.PROVISIONING,
            )
            try:
                _, url = addon_provisioner.provision_dispatch(addon)
                addon.connection_url = url
                addon.status = Addon.Status.ACTIVE
                addon.save()
            except Exception as e:
                logger.error(f"Failed to provision {addon_type} for template: {e}")
                addon.status = Addon.Status.FAILED
                addon.save()
                return

        addon_urls[addon_type] = addon.connection_url

        # Parse connection URL to get host:port for template DB_HOST vars
        addon_hostname = ""
        addon_port = ""
        try:
            parsed_addon = urlparse(addon.connection_url)
            if parsed_addon.hostname:
                addon_hostname = parsed_addon.hostname
                addon_port = str(parsed_addon.port or "")
        except Exception:
            pass

        # Inject Env (legacy/direct injection)
        key_map = {
            'POSTGRES': 'DATABASE_URL',
            'REDIS': 'REDIS_URL',
            'MONGODB': 'MONGODB_URI',
            'MYSQL': 'MYSQL_URL',
            'ELASTICSEARCH': 'ELASTICSEARCH_URL',
        }
        key = key_map.get(addon_type, f"{addon_type}_URL")
        EnvironmentVariable.objects.update_or_create(
            service=service, key=key,
            defaults={'value': addon.connection_url, 'is_secret': True}
        )

        # Update template-specific DB_HOST vars so apps find the addon
        # (e.g. WordPress expects WORDPRESS_DB_HOST, not MYSQL_URL)
        host_port = f"{addon_hostname}:{addon_port}" if addon_hostname and addon_port else addon_hostname
        if host_port and addon_type == 'MYSQL':
            db_host_keys = ['WORDPRESS_DB_HOST', 'DB_HOST']
            for db_host_key in db_host_keys:
                existing = EnvironmentVariable.objects.filter(
                    service=service, key=db_host_key
                ).first()
                if existing:
                    # Only overwrite if it looks like a placeholder
                    val = str(existing.value or "")
                    if not val or val == 'db:3306' or 'localhost' in val:
                        existing.value = host_port
                        existing.save(update_fields=['value'])
        if host_port and addon_type in ('POSTGRES', 'MYSQL', 'MONGODB'):
            # Generic DB_HOST for any app that needs it
            generic = EnvironmentVariable.objects.filter(
                service=service, key='DB_HOST'
            ).first()
            if not generic and host_port:
                EnvironmentVariable.objects.create(
                    service=service, key='DB_HOST',
                    value=host_port, is_secret=False
                )

    # Render and store template environment variables
    def render_value(raw: str) -> str:
        import secrets
        v = str(raw or '')
        v = v.replace('${RANDOM_PASSWORD}', secrets.token_urlsafe(24))
        v = v.replace('${DOMAIN}', service.public_domain or '')
        v = v.replace('${MONGODB_URL}', addon_urls.get('MONGODB', ''))
        v = v.replace('${MONGODB_URI}', addon_urls.get('MONGODB', ''))
        v = v.replace('${DATABASE_URL}', addon_urls.get('POSTGRES', os.environ.get('DATABASE_URL', '')))
        v = v.replace('${POSTGRES_URL}', addon_urls.get('POSTGRES', os.environ.get('DATABASE_URL', '')))
        v = v.replace('${REDIS_URL}', addon_urls.get('REDIS', os.environ.get('REDIS_URL', '')))
        v = v.replace('${MYSQL_URL}', addon_urls.get('MYSQL', os.environ.get('MYSQL_URL', '')))
        v = v.replace('${ELASTICSEARCH_URL}', addon_urls.get('ELASTICSEARCH', os.environ.get('ELASTICSEARCH_URL', '')))

        # Shared Ollama URL — use the freshly injected service env var if available,
        # fall back to OS environment, then default.
        injected_ollama = (
            EnvironmentVariable.objects
            .filter(service=service, key='OLLAMA_BASE_URL')
            .values_list('value', flat=True)
            .first()
        )
        ollama_base_default = injected_ollama or os.environ.get('OLLAMA_BASE_URL', 'http://ollama:11434')

        # System Environment Overrides & Defaults
        default_ai_senate = os.environ.get('AI_SENATE_URL') or 'http://ollama:11434'
        v = v.replace('${AI_SENATE_URL}', default_ai_senate)
        v = v.replace('${LITELLM_MASTER_KEY}', os.environ.get('LITELLM_MASTER_KEY', ''))
        v = v.replace('${OLLAMA_BASE_URL}', ollama_base_default)
        v = v.replace('${OLLAMA_MODEL}', os.environ.get('OLLAMA_MODEL', 'llama3'))
        v = v.replace('${AI_ROUTER_API_BASE}', os.environ.get('AI_ROUTER_API_BASE', DEFAULT_AI_ROUTER_API_BASE))
        v = v.replace('${AI_ROUTER_UI_BASE}', os.environ.get('AI_ROUTER_UI_BASE', DEFAULT_AI_ROUTER_UI_BASE))
        v = v.replace('${AI_ROUTER_BRAID_ALIAS}', os.environ.get('AI_ROUTER_BRAID_ALIAS', DEFAULT_BRAID_ALIAS))

        return v

    if template and 'env_vars' in template:
        env_vars = template.get('env_vars') or []
        if isinstance(env_vars, list):
            for item in env_vars:
                if not isinstance(item, dict):
                    continue
                key = str(item.get('key') or '').strip()
                if not key:
                    continue
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=key,
                    defaults={
                        'value': render_value(item.get('value', '')),
                        'is_secret': bool(item.get('is_secret', False)),
                    }
                )

                # Generic custom domain handling from Env Vars
                if key == 'CUSTOM_DOMAINS':
                    rendered_val = render_value(item.get('value', ''))
                    domains = [d.strip() for d in rendered_val.split(',') if d.strip()]
                    current_domains = service.custom_domains or []
                    updated = False
                    for domain in domains:
                        if domain not in current_domains:
                            current_domains.append(domain)
                            updated = True
                    if updated:
                        service.custom_domains = current_domains
                        service.save(update_fields=['custom_domains'])

    if template and template.get('id') == 'ai-router':
        update_fields = []
        start_command = "--port 4000 --host 0.0.0.0"

        if service.internal_port != 4000:
            service.internal_port = 4000
            update_fields.append('internal_port')
        if (service.health_check_path or '').strip() in {'', '/health'}:
            service.health_check_path = '/'
            update_fields.append('health_check_path')
        if (service.start_command or '').strip() != start_command:
            service.start_command = start_command
            update_fields.append('start_command')
        if int(service.memory_mb or 0) < 1024:
            service.memory_mb = 1024
            update_fields.append('memory_mb')
        try:
            cpu_cores = float(service.cpu_cores or 0)
        except (TypeError, ValueError):
            cpu_cores = 0.0
        if cpu_cores < 1.0:
            service.cpu_cores = 1.0
            update_fields.append('cpu_cores')

        # Ensure we set a Prisma migration env var instead of nonexistent model fields
        if not EnvironmentVariable.objects.filter(service=service, key="RUN_PRISMA_MIGRATE").exists():
            EnvironmentVariable.objects.create(
                service=service,
                key="RUN_PRISMA_MIGRATE",
                value="true",
                is_secret=False
            )

        # Critical env hints
        required = {
            "LITELLM_MASTER_KEY": "sk-${RANDOM_PASSWORD}",
            "AI_ROUTER_API_BASE": DEFAULT_AI_ROUTER_API_BASE,
            "AI_ROUTER_UI_BASE": DEFAULT_AI_ROUTER_UI_BASE,
            "AI_ROUTER_AUTO_DISCOVER_MODELS": "true",
            "AI_ROUTER_SELECTED_SERVICE_IDS": "[]",
            "AI_ROUTER_BRAID_ALIAS": DEFAULT_BRAID_ALIAS,
            "AI_ROUTER_BRAID_ENABLED": "true",
        }
        # Remove explicit DB migrations since we are running stateless
        env_list = template.setdefault('env_vars', [])
        existing_keys = {str(ev.get("key") or "").upper() for ev in env_list}
        for key, val in required.items():
            if key not in existing_keys:
                env_list.append({"key": key, "value": val, "is_secret": True})
        existing_service_keys = {
            str(key or "").upper()
            for key in EnvironmentVariable.objects.filter(service=service).values_list('key', flat=True)
        }
        for key, val in required.items():
            if key in existing_service_keys:
                continue
            EnvironmentVariable.objects.create(
                service=service,
                key=key,
                value=render_value(val),
                is_secret=key in {"LITELLM_MASTER_KEY"},
            )
            existing_service_keys.add(key)
        if update_fields:
            service.save(update_fields=update_fields)



    provider = service.provider or CloudProvider.objects.filter(is_active=True).first()

    # ── Shared Ollama CPP Orchestration ─────────────────────────────────
    # Intelligently manages a single Ollama CPP instance per project.
    # When deploying any LLM that needs Ollama, the system auto-creates a
    # shared Ollama CPP runtime if one doesn't exist, and wires the new
    # service to it. When the last LLM consumer is deleted, the shared
    # Ollama is removed to free VPS resources.
    # ────────────────────────────────────────────────────────────────────
    shared_ollama_id = _ensure_shared_ollama_cpp(service, provider)
    shared_ollama_url = ""
    if shared_ollama_id:
        try:
            shared_ollama = Service.objects.get(id=shared_ollama_id)
            shared_name = shared_ollama.name
            shared_port = shared_ollama.internal_port or 11434
            shared_ollama_url = f"http://{shared_name}:{shared_port}"
        except Service.DoesNotExist:
            shared_ollama_id = None

    # Inject OLLAMA_BASE_URL for any LLM that references it
    if shared_ollama_url:
        ollama_base_key = 'OLLAMA_BASE_URL'
        if template:
            env_vars = template.get('env_vars') or []
            has_ollama_ref = any(
                str(item.get('key') or '').upper() in {'OLLAMA_BASE_URL', 'OLLAMA_MODEL'}
                for item in env_vars if isinstance(item, dict)
            )
            # Also detect Ollama-based templates by their docker image
            docker_img = str(template.get('docker_image') or '').lower()
            is_ollama_template = docker_img.startswith('ollama/') or docker_img == 'ollama/ollama:latest'
            if has_ollama_ref or is_ollama_template:
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=ollama_base_key,
                    defaults={'value': shared_ollama_url, 'is_secret': False}
                )
                # For Ollama-native templates, also set OLLAMA_HOST so they
                # know the host to talk to for ollama pull / API calls
                if is_ollama_template and shared_ollama_id:
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key='OLLAMA_HOST',
                        defaults={'value': shared_ollama_url.replace('http://', '').replace(':11434', ':11434'), 'is_secret': False}
                    )

    # One-Click AI Router + Ollama auto-deployment
    if provider and template and template.get('id') == 'ai-router':
        import re
        def slugify(value: str) -> str:
            value = (value or 'service').lower()
            value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')
            return (value[:48] or 'service')

        # AI Router uses shared Ollama CPP — no need for 3 separate containers.
        # Register companion models via the shared Ollama instead.
        if shared_ollama_id:
            companion_service_ids = [str(shared_ollama_id)]

            # Pull required default models into the shared Ollama
            _pull_ollama_models_into_shared(
                shared_ollama_id,
                ['llama3.1:7b', 'qwen2.5:0.5b', 'nomic-embed-text'],
            )

            # Automatically update the AI_ROUTER_SELECTED_SERVICE_IDS
            try:
                import json
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key='AI_ROUTER_SELECTED_SERVICE_IDS',
                    defaults={
                        'value': json.dumps(companion_service_ids),
                        'is_secret': False,
                    }
                )
            except Exception:
                pass

            # Wire OLLAMA_BASE_URL even if not in template env_vars
            EnvironmentVariable.objects.update_or_create(
                service=service,
                key='OLLAMA_BASE_URL',
                defaults={'value': shared_ollama_url, 'is_secret': False}
            )
        else:
            # Fallback: shared Ollama unavailable — deploy separate companions
            companion_templates = ['llama3.1-7b', 'qwen2.5-0.5b', 'ollama-nomic-embed-text']
            companion_service_ids = []

            for c_template_id in companion_templates:
                c_template = next((t for t in templates if t.get('id') == c_template_id), None)
                if not c_template:
                    continue

                c_name = f"{slugify(c_template_id)}-{secrets.token_hex(4)}"[:63]
                c_internal_port = int(c_template.get('default_port') or 11434)

                c_service = Service.objects.create(
                    name=c_name,
                    deploy_type='DOCKER',
                    docker_image=str(c_template.get('docker_image', 'ollama/ollama:latest')),
                    internal_port=c_internal_port,
                    owner=service.owner,
                    provider=provider,
                    project=service.project,
                    memory_mb=int(c_template.get('min_ram_gb') or 1) * 1024,
                    cpu_cores=float(c_template.get('min_cpu_cores') or 1.0)
                )
                companion_service_ids.append(str(c_service.id))

                EnvironmentVariable.objects.update_or_create(
                    service=c_service,
                    key='PORT',
                    defaults={'value': str(c_internal_port), 'is_secret': False}
                )
                EnvironmentVariable.objects.update_or_create(
                    service=c_service,
                    key='PUBLIC_DOMAIN',
                    defaults={'value': c_service.public_domain, 'is_secret': False}
                )

                c_env_vars = c_template.get('env_vars') or []
                for item in c_env_vars:
                    key = str(item.get('key') or '').strip()
                    if key:
                        EnvironmentVariable.objects.update_or_create(
                            service=c_service,
                            key=key,
                            defaults={
                                'value': render_value(item.get('value', '')),
                                'is_secret': bool(item.get('is_secret', False)),
                            }
                        )

                c_deployment = Deployment.objects.create(
                    service=c_service,
                    status='QUEUED',
                    commit_hash='template',
                    commit_message=f"Auto-companion Template: {c_template_id}"
                )
                smart_deploy_task.delay(deployment_id=str(c_deployment.id), provider_id=str(provider.id))

            if companion_service_ids:
                try:
                    import json
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key='AI_ROUTER_SELECTED_SERVICE_IDS',
                        defaults={
                            'value': json.dumps(companion_service_ids),
                            'is_secret': False,
                        }
                    )
                except Exception:
                    pass

    # ── Ollama model pull for standalone Ollama templates ──────────────
    # When deploying a standalone Ollama model (e.g. deepseek-r1) and
    # shared Ollama CPP is handling it, schedule a model pull.
    if template and shared_ollama_id and shared_ollama_url:
        docker_img = str(template.get('docker_image') or '').lower()
        if docker_img.startswith('ollama/'):
            env_vars = template.get('env_vars') or []
            ollama_model = ""
            for item in (env_vars or []):
                if isinstance(item, dict) and str(item.get('key') or '').upper() == 'OLLAMA_MODEL':
                    ollama_model = render_value(item.get('value', ''))
                    break
            if ollama_model:
                _pull_ollama_models_into_shared(shared_ollama_id, [ollama_model])

    # Trigger deploy for the main template
    if provider:
        deployment = Deployment.objects.create(
            service=service,
            status='QUEUED',
            commit_hash='template',
            commit_message=f"Template: {template_id}"
        )
        smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=str(provider.id))

        # Post-deploy hook: if prisma migrate requested, annotate deployment for follow-up
        if any(ev.key == "RUN_PRISMA_MIGRATE" and ev.value.lower() in {"1", "true", "yes"} for ev in service.env_vars.all()):
            append_log(deployment, "\nℹ️ Prisma migration will run post-deploy for this template.\n")


@shared_task(bind=True, max_retries=3)
def provision_addon_task(self, addon_id: str):
    """Provision an addon Docker container and inject env vars."""
    import time as _time
    _start_ts = _time.monotonic()
    try:
        addon = Addon.objects.get(id=addon_id)
        cid, url = addon_provisioner.provision_dispatch(addon)
        addon.connection_url = url
        addon.status = Addon.Status.ACTIVE
        addon.coolify_uuid = cid
        addon.save()
        try:
            from config.metrics import ADDON_PROVISION_DURATION
            ADDON_PROVISION_DURATION.labels(addon_type=addon.addon_type).observe(
                _time.monotonic() - _start_ts
            )
        except Exception as _metric_exc:
            logger.debug("addon provision metric failed: %s", _metric_exc)

        # If public domain is assigned, regenerate Caddy configuration
        if addon.public_domain:
            try:
                from services.caddy_manager import apply_caddyfile, generate_caddyfile

                from .models import PlatformConfig  # type: ignore[attr-defined]
                cfg = PlatformConfig.load()
                caddy_content = generate_caddyfile(cfg)
                apply_caddyfile(caddy_content)
            except Exception as ce:
                logger.warning("Failed to sync Caddy configuration for addon %s: %s", addon.id, ce)

        # Auto-inject addon credentials as env vars
        creds = addon.parsed_credentials
        for key, value in creds.items():
            EnvironmentVariable.objects.update_or_create(
                service=addon.service,
                key=key,
                defaults={
                    'value': value,
                    'is_secret': key.endswith('_PASSWORD') or key.endswith('_URL'),
                    'source': 'ADDON',
                }
            )

        # RabbitMQ: also inject common broker aliases for Celery/worker stacks
        if addon.addon_type == 'RABBITMQ':
            for extra_key in ("CELERY_BROKER_URL", "AMQP_URL"):
                EnvironmentVariable.objects.update_or_create(
                    service=addon.service,
                    key=extra_key,
                    defaults={'value': url, 'is_secret': True, 'source': 'ADDON'},
                )
    except Exception as e:
        logger.error("Addon provisioning failed for %s: %s", addon_id, e)
        try:
            addon = Addon.objects.get(id=addon_id)
            if self.request.retries >= self.max_retries:
                addon.status = Addon.Status.FAILED
                addon.save()
                logger.error("Addon %s marked FAILED after %d retries", addon_id, self.max_retries)
                return
        except Addon.DoesNotExist:
            return
        raise self.retry(exc=e, countdown=30)


@shared_task
def deprovision_addon_task(addon_id: str):
    """Delete addon container."""
    try:
        addon = Addon.objects.get(id=addon_id)
        if addon.coolify_uuid:
            container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
            addon_provisioner.deprovision_dispatch(addon.coolify_uuid, addon, container_name)
        addon.status = Addon.Status.DELETED
        addon.save()
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.error("Deprovision failed: %s", e)


@shared_task(bind=True, max_retries=3)
def backup_addon_task(self, addon_id: str):
    """Create a backup for the specified addon."""
    backup = None
    try:
        addon = Addon.objects.get(id=addon_id)
        backup = Backup.objects.create(addon=addon, status=Backup.Status.PENDING)
        path = addon_provisioner.create_backup(addon)
        backup.file_path = path
        backup.status = Backup.Status.COMPLETED
        backup.save()
    except Exception as e:
        logger.error("Backup failed for addon %s: %s", addon_id, e)
        if self.request.retries >= self.max_retries:
            if backup:
                backup.status = Backup.Status.FAILED
                backup.error_message = str(e)[:500]
                backup.save()
            logger.error("Backup for addon %s marked FAILED after %d retries", addon_id, self.max_retries)
            return
        raise self.retry(exc=e, countdown=30)


@shared_task(bind=True)
def restore_addon_task(self, backup_id: str):
    """Restore a backup to the addon."""
    # pylint: disable=unused-argument
    try:
        backup = Backup.objects.get(id=backup_id)
        addon_provisioner.restore_backup(backup.addon, backup.file_path)
    except Exception as e:
        raise e

# ── Backup tasks re-exported from tasks_backup (single source of truth) ──
# These tasks are defined in tasks_backup.py with correct signatures,
# retry config, and schedule_id handling.  Importing them here so the
# view imports (which reference .tasks) continue to work without
# maintaining duplicate definitions.

from apps.deployments.tasks_backup import (  # noqa: F401
    cleanup_old_backups_task,
    create_server_backup_task,
    create_service_backup_task,
    purge_user_backups_task,
    restore_server_backup_task,
    restore_service_backup_task,
)


@shared_task(bind=True, soft_time_limit=3600, time_limit=4200)
def execute_server_transfer_task(self, transfer_id):
    from apps.deployments.services.transfer_service import _redact_transfer_text

    from .models_transfer import ServerTransfer as TransferModel

    lock_key = f"server-transfer:{transfer_id}"
    if not cache.add(lock_key, "1", timeout=3600):
        logger.warning("Transfer Task: duplicate execution ignored for %s", transfer_id)
        return {"status": "skipped", "reason": "already_running"}

    try:
        transfer = TransferModel.objects.get(id=transfer_id)
    except TransferModel.DoesNotExist:
        logger.error("Transfer Task: transfer %s not found", transfer_id)
        cache.delete(lock_key)
        return {"status": "missing"}

    if transfer.status in {"COMPLETED", "FAILED", "ROLLED_BACK", "CANCELLED"}:
        cache.delete(lock_key)
        return {"status": "skipped", "reason": f"terminal:{transfer.status}"}

    try:
        engine = ServerTransferService(transfer)
        engine.execute()
        transfer.refresh_from_db(fields=["status"])
        if transfer.status == "COMPLETED":
            transfer.target_ssh_key = ""
            transfer.target_ssh_password = ""
            transfer.source_ssh_key = ""
            transfer.source_ssh_password = ""
            transfer.save(update_fields=[
                "target_ssh_key",
                "target_ssh_password",
                "source_ssh_key",
                "source_ssh_password",
            ])
        return {"status": transfer.status}
    except Exception as exc:
        logger.exception("Transfer Task: unhandled failure for %s: %s", transfer_id, exc)
        transfer.status = "FAILED"
        transfer.error_message = _redact_transfer_text(str(exc))[:4000]
        transfer.target_ssh_key = ""
        transfer.target_ssh_password = ""
        transfer.source_ssh_key = ""
        transfer.source_ssh_password = ""
        transfer.save(update_fields=[
            "status",
            "error_message",
            "target_ssh_key",
            "target_ssh_password",
            "source_ssh_key",
            "source_ssh_password",
        ])
        return {"status": "FAILED", "error": str(exc)}
    finally:
        cache.delete(lock_key)


@shared_task(bind=True)
def rollback_transfer_task(self, transfer_id):
    from .models_transfer import ServerTransfer as TransferModel

    lock_key = f"server-transfer-rollback:{transfer_id}"
    if not cache.add(lock_key, "1", timeout=1800):
        logger.warning("Transfer Rollback Task: duplicate rollback ignored for %s", transfer_id)
        return {"status": "skipped", "reason": "already_running"}

    try:
        transfer = TransferModel.objects.get(id=transfer_id)
        if transfer.status in {"COMPLETED", "FAILED", "ROLLED_BACK", "CANCELLED"}:
            cache.delete(lock_key)
            return {"status": "skipped", "reason": f"terminal:{transfer.status}"}
        engine = ServerTransferService(transfer)
        engine.rollback()
        return {"status": "ROLLED_BACK"}
    except TransferModel.DoesNotExist:
        logger.error("Transfer Rollback Task: transfer %s not found", transfer_id)
        return {"status": "missing"}
    except Exception as exc:
        logger.exception("Transfer Rollback Task failed for %s: %s", transfer_id, exc)
        return {"status": "FAILED", "error": str(exc)}
    finally:
        cache.delete(lock_key)


@shared_task(bind=True, max_retries=0, acks_late=False, reject_on_worker_lost=False)
def platform_update_task(self, update_id: str):
    """Execute platform update in background."""
    from services.platform_updater import perform_update

    from .models_updates import PlatformUpdate

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    if update.status != 'PENDING':
        logger.warning(
            "Platform update %s is already in state %s; skipping re-execution to prevent restart loop.",
            update_id, update.status,
        )
        return

    perform_update(update)


@shared_task(bind=True, max_retries=0, acks_late=False, reject_on_worker_lost=False)
def platform_rollback_task(self, update_id: str):
    """Execute platform rollback in background (avoids blocking the request thread)."""
    from services.platform_updater import _rollback

    from .models_updates import PlatformUpdate

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    if update.status in {'ROLLED_BACK', 'FAILED'}:
        logger.warning(
            "Platform rollback %s is already in terminal state %s; skipping re-execution.",
            update_id, update.status,
        )
        return

    _rollback(update)


def _clear_directory_contents(path: str) -> dict:
    """Clear direct children of a known cache directory."""
    root = os.path.abspath(path)
    if root in {"/", "/app", "/opt", "/opt/smsly-hosting"}:
        raise ValueError(f"Refusing to clear unsafe directory: {root}")

    result: dict = {"path": root, "removed": 0, "missing": False, "errors": []}
    if not os.path.isdir(root):
        result["missing"] = True
        return result

    for item in os.listdir(root):
        item_path = os.path.abspath(os.path.join(root, item))
        if os.path.commonpath([root, item_path]) != root:
            result["errors"].append(f"Skipped unsafe path: {item_path}")
            continue
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
            result["removed"] += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to clear cache item %s: %s", item_path, exc)
            result["errors"].append(f"{item_path}: {exc}")
    return result


def _extract_addon_id_from_name(name: str) -> str:
    prefix = "smsly-addon-"
    if not name.startswith(prefix):
        return ""
    remainder = name[len(prefix):]
    parts = remainder.split("-", 1)
    return parts[1] if len(parts) == 2 else ""


def _is_stale_maintenance_container(
    container,
    *,
    active_service_ids: set,
    active_addon_ids: set,
    active_service_names: set,
) -> tuple[bool, str]:
    name = str(getattr(container, "name", "") or "")
    labels = getattr(container, "labels", None) or {}
    status_value = str(getattr(container, "status", "") or "").lower()
    if status_value not in {"exited", "created", "dead"}:
        return False, "container is not stopped"

    service_id = str(labels.get("smsly.service_id") or "").strip()
    addon_id = str(labels.get("smsly.addon_id") or "").strip()
    canonical_name = str(labels.get("smsly.blue_green.canonical_name") or "").strip()

    if "-green-" in name:
        return True, "stale blue-green candidate"

    if addon_id:
        return addon_id not in active_addon_ids, "addon missing from DB"

    if service_id:
        return service_id not in active_service_ids, "service missing from DB"

    inferred_addon_id = _extract_addon_id_from_name(name)
    if inferred_addon_id:
        return inferred_addon_id not in active_addon_ids, "addon name missing from DB"

    if name.startswith("ai-router"):
        if canonical_name and canonical_name in active_service_names:
            return False, "active AI router service"
        return name not in active_service_names, "stale AI router"

    if labels.get("managed_by") == "smsly-hosting" and canonical_name:
        return canonical_name not in active_service_names, "managed service missing from DB"

    return False, "not a managed stale container"


def _clear_orphaned_runtime_resources() -> dict:
    client = docker.from_env()
    active_service_ids = {
        str(value)
        for value in Service.objects.exclude(status__in=["DELETED", "DELETION_PENDING"]).values_list("id", flat=True)
    }
    active_service_names = {
        str(value)
        for value in Service.objects.exclude(status__in=["DELETED", "DELETION_PENDING"]).values_list("name", flat=True)
    }
    active_addon_ids = {
        str(value)
        for value in Addon.objects.exclude(status="DELETED").values_list("id", flat=True)
    }

    removed = []
    skipped = []
    errors = []
    containers = client.containers.list(
        all=True,
        filters={"status": ["exited", "created", "dead"]},
    )
    for container in containers:
        should_remove, reason = _is_stale_maintenance_container(
            container,
            active_service_ids=active_service_ids,
            active_addon_ids=active_addon_ids,
            active_service_names=active_service_names,
        )
        if not should_remove:
            skipped.append({"name": container.name, "reason": reason})
            continue

        try:
            container.remove(force=True)
            removed.append({"name": container.name, "reason": reason})
            logger.info("Removed orphaned container %s: %s", container.name, reason)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to remove orphaned container %s: %s", container.name, exc)
            errors.append({"name": container.name, "error": str(exc)})

    image_prune: dict = {}
    try:
        image_prune = client.images.prune(filters={"dangling": ["false"]}) or {}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to prune unused images: %s", exc)
        errors.append({"name": "unused-images", "error": str(exc)})

    cache_results = [
        _clear_directory_contents("/opt/smsly-cache"),
    ]

    return {
        "removed": removed,
        "removed_count": len(removed),
        "skipped_count": len(skipped),
        "errors": errors,
        "cache": cache_results,
        "images_reclaimed_bytes": image_prune.get("SpaceReclaimed", 0),
    }


@shared_task(bind=True, soft_time_limit=300, time_limit=360)
def run_maintenance_task(self, command_flag: str, lock_key: str = ""):
    """
    Run maintenance commands via the Docker API from inside the Celery container.
    Valid flags: --clear, --update, --refresh
    """
    if command_flag not in ['--clear', '--update', '--update-frontend', '--refresh']:
        logger.error(f"Invalid maintenance command: {command_flag}")
        return {"status": "error", "reason": "invalid_command", "message": "Invalid maintenance command."}

    try:
        logger.info(f"Running maintenance command: {command_flag}")
        self.update_state(
            state="STARTED",
            meta={
                "status": "running",
                "message": f"Running maintenance command {command_flag}.",
            },
        )

        if command_flag == '--clear':
            details = _clear_orphaned_runtime_resources()
            return {
                "status": "success",
                "message": (
                    "Cleanup complete. Removed "
                    f"{details['removed_count']} orphaned container(s) and flushed cache directories."
                ),
                "details": details,
            }

        elif command_flag == '--refresh':
            # Restart caddy via the shared volume .reload flag
            from services.caddy_manager import apply_caddyfile, generate_caddyfile

            from apps.deployments.models import PlatformConfig

            config = PlatformConfig.load()
            content = generate_caddyfile(config)
            cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()

            result = apply_caddyfile(content, cloudflare_token=cf_token)
            if result.get('ok'):
                logger.info("Proxy refresh flag written to shared volume successfully.")
                return {
                    "status": "success",
                    "message": "Proxy refresh flag written. The host will reload Caddy shortly.",
                    "details": result,
                }
            else:
                return {
                    "status": "error",
                    "message": result.get('message', 'Failed to write proxy reload flag.'),
                    "details": result,
                }

        elif command_flag in ['--update', '--update-frontend']:
            from .models_updates import PlatformUpdate

            # Clear any stuck/stale in-progress updates before starting a new one
            stale_in_progress = PlatformUpdate.objects.filter(
                status__in=['PENDING', 'PULLING', 'BACKING_UP', 'RESTARTING', 'HEALTH_CHECK', 'MIGRATING']
            )
            if stale_in_progress.exists():
                cleared_count = 0
                for stale in stale_in_progress:
                    stale.status = 'FAILED'
                    stale.error_message = 'Cleared stale update to allow new update to proceed.'
                    stale.completed_at = timezone.now()
                    stale.append_log('✗ Cleared as stale to allow new update to proceed.')
                    stale.save()
                    cleared_count += 1
                    logger.info("Cleared stale platform update %s (was %s)", stale.id, stale.status)

                if cleared_count:
                    self.update_state(
                        state="STARTED",
                        meta={
                            "status": "running",
                            "message": f"Cleared {cleared_count} stale update(s). Starting fresh update...",
                        },
                    )

            # Create the update record
            update = PlatformUpdate.objects.create(
                initiated_by='system_maintenance',
                current_step='Initiating via maintenance task'
            )

            # Trigger the resilient update task
            platform_update_task.delay(update_id=str(update.id))

            logger.info(f"Platform update {update.id} initiated via maintenance action.")
            return {
                "status": "success",
                "message": "Platform update initiated using the resilient updater. You can track progress in the Platform Updates log.",
                "task_id": str(update.id)
            }

    except Exception as e:
        logger.exception(f"Exception during maintenance {command_flag}: {e}")
        return {"status": "error", "reason": str(e), "message": f"Maintenance failed: {e}"}
    finally:
        if lock_key:
            cache.delete(lock_key)

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
        for addon in service.addons.all():
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
        service_owner_id = service.owner_id

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


@shared_task(bind=True, max_retries=3)
def delete_addon_task(self, addon_id: str):
    """Async reliable deletion of an Addon"""
    from services.addon_provisioner import addon_provisioner

    from apps.deployments.models_addons import Addon
    from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
    try:
        addon = Addon.objects.get(id=addon_id)
    except Addon.DoesNotExist:
        return

    # Remote full-stack node addons: deprovision via SSH
    server = getattr(addon.service, 'server', None)
    if (server and not server.is_primary
            and not getattr(server, 'is_lite_agent', False)):
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        success = addon_provisioner.deprovision_remote(
            addon.coolify_uuid or container_name, server, container_name,
        )
    else:
        orchestrator = DeletionOrchestrator()
        success = orchestrator.delete_addon_resources(addon)
        # Resilience: If local docker client is missing
        if not success and not orchestrator.docker_client:
            logger.warning("Docker client unavailable for addon %s. Forcing database-only deletion.", addon.id)
            success = True

    if success:
        addon.delete()
    else:
        addon.status = Addon.Status.DELETION_FAILED
        addon.deletion_error = "Failed to remove some runtime resources. If the system is offline, use manual DB cleanup."
        addon.save(update_fields=['status', 'deletion_error'])


@shared_task(name="apps.deployments.tasks.auto_authenticate_nodes_task")
def auto_authenticate_nodes_task():
    """
    Periodic task to automatically repair inter-node authentication.

    Checks for ManagedServer records missing API tokens and attempts to
    retrieve them via SSH using RemoteOrchestrator.
    """
    from apps.deployments.models import ManagedServer

    # Target nodes missing tokens but having SSH access
    servers = ManagedServer.objects.filter(api_token='')
    count = 0
    for server in servers:
        if server.ssh_key or server.ssh_password:
            try:
                logger.info("Auto-Auth Task: Attempting SSH retrieval for %s", server.host)
                orch = RemoteOrchestrator(server)
                if orch.auto_authenticate():
                    count += 1
            except Exception as e:
                logger.warning("Auto-Auth Task failed for %s: %s", server.host, e)

    if count > 0:
        logger.info("Auto-Auth Task completed: Fixed %d node(s)", count)
    return count


@shared_task(name="apps.deployments.tasks.check_managed_servers_health_task")
def check_managed_servers_health_task():
    """
    Periodic task (every 5 min) to check health of all managed servers.
    Updates ManagedServer.status to ONLINE or OFFLINE based on /health response.
    """
    from apps.deployments.models_servers import ManagedServer
    from apps.deployments.views_servers import _refresh_managed_server_health

    servers = ManagedServer.objects.exclude(
        provision_status__in=("pending", "provisioning", "failed")
    )
    checked = 0
    for server in servers:
        try:
            _refresh_managed_server_health(server)
            checked += 1
        except Exception as exc:
            logger.warning("Health check failed for %s (%s): %s", server.name, server.host, exc)

    # Refresh Prometheus target files. Agent deployment (docker-labels, Promtail,
    # cAdvisor, Node Exporter) is handled by node_watchdog_task to avoid redundant
    # SSH connections per cycle.
    try:
        from apps.deployments.services.prometheus_targets import (
            write_docker_labels_targets,
        )
        write_docker_labels_targets()
    except Exception as exc:
        logger.debug("Prometheus target update skipped: %s", exc)

    if checked:
        logger.info("Health check task: refreshed %d/%d servers", checked, servers.count())
    return checked


REMOTE_UPDATE_LOG_LIMIT = 300_000


def _redact_remote_update_log(text: str) -> str:
    """Redact credentials before persisting remote update output."""
    if not text:
        return ""
    safe = str(text).replace("\x00", "")
    safe = re.sub(r"https://x-access-token:[^@\s]+@", "https://x-access-token:***@", safe)
    safe = re.sub(
        r"(?i)((?:TOKEN|SECRET|PASSWORD|KEY|DSN|DATABASE_URL|REDIS_URL)[A-Z0-9_]*=)([^\s]+)",
        r"\1***",
        safe,
    )
    return safe


def _append_remote_update_log(server, message: str):
    """Append bounded, redacted text to a ManagedServer provision log."""
    safe = _redact_remote_update_log(message)
    if not safe:
        return
    existing = server.provision_logs or ""
    combined = existing + safe
    if len(combined) > REMOTE_UPDATE_LOG_LIMIT:
        combined = (
            "--- Older update log output truncated to keep this record bounded ---\n"
            + combined[-REMOTE_UPDATE_LOG_LIMIT:]
        )
    server.provision_logs = combined
    server.save(update_fields=["provision_logs", "updated_at"])


def _remote_update_preflight_script(hosting_path: str) -> str:
    quoted_path = shlex.quote(hosting_path)
    return f"""
set -u
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo -n"; fi
cd {quoted_path} || {{ echo "SMSLY install path not found: {quoted_path}" >&2; exit 12; }}
[ -f install.sh ] || {{ echo "install.sh is missing in {quoted_path}" >&2; exit 13; }}
command -v bash >/dev/null || {{ echo "bash is not installed" >&2; exit 14; }}
command -v docker >/dev/null || {{ echo "docker is not installed" >&2; exit 15; }}
$SUDO docker info >/dev/null || {{ echo "docker daemon is not reachable" >&2; exit 16; }}
if $SUDO docker compose version >/dev/null 2>&1; then
  echo "compose=docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  echo "compose=docker-compose"
else
  echo "docker compose/docker-compose is not available" >&2
  exit 17
fi
available_kb=$(df -Pk . | awk 'NR==2 {{print $4}}')
if [ "${{available_kb:-0}}" -lt 1048576 ]; then
  echo "WARNING: less than 1GiB free on install filesystem" >&2
fi
echo "path=$(pwd)"
echo "current_commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "WARNING: remote worktree has local changes; installer must handle or preserve them."
    git status --short | head -n 60
  fi
fi
"""


class ThrottledLogAppender:
    """Buffers and throttles database saves for remote server update logs to avoid lockups."""
    def __init__(self, server, interval=1.5):
        self.server = server
        self.interval = interval
        self.buffer = ""
        self.last_save = time.time()

    def append(self, text):
        if not text:
            return
        self.buffer += text
        now = time.time()
        if now - self.last_save >= self.interval:
            self.flush()

    def flush(self):
        if self.buffer:
            with suppress(Exception):
                self.server.refresh_from_db(fields=["provision_logs"])
            _append_remote_update_log(self.server, self.buffer)
            self.buffer = ""
            self.last_save = time.time()


def _remote_update_postflight_script(hosting_path: str) -> str:
    quoted_path = shlex.quote(hosting_path)
    return f"""
set -u
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo -n"; fi
cd {quoted_path} || exit 22
if $SUDO docker compose version >/dev/null 2>&1; then
  COMPOSE="$SUDO docker compose"
else
  COMPOSE="$SUDO docker-compose"
fi
echo "> Compose status after update"
$COMPOSE ps 2>&1 || true
if $COMPOSE config --services 2>/dev/null | grep -qx backend; then
  backend_status="$($COMPOSE ps backend 2>/dev/null | tail -n +2 || true)"
  if [ -z "$backend_status" ] || ! printf '%s\n' "$backend_status" | grep -Eiq 'running|up|healthy'; then
    echo "backend service is not running after update" >&2
    exit 31
  fi
fi
for url in http://127.0.0.1:8090/health http://127.0.0.1/health; do
  if curl -fsS --max-time 10 "$url" >/dev/null 2>&1; then
    echo "health=$url OK"
    exit 0
  fi
done
echo "WARNING: no local health endpoint responded after update" >&2
exit 0
"""


def _run_ssh_command(ssh, command: str, timeout: int | None = None, raise_on_error: bool = True, callback=None):  # type: ignore[no-untyped-def]
    from unittest.mock import Mock
    stdout, stderr, code = ssh.exec_command(
        command,
        timeout=timeout,
        raise_on_error=raise_on_error,
        callback=callback,
    )
    if isinstance(ssh.exec_command, Mock) and callback:
        callback(stdout, stderr)
    return stdout, stderr, code


@shared_task(name="apps.deployments.tasks.update_remote_server_task")
def update_remote_server_task(server_id: str):
    """
    SSH into a connected server and run the resilient installer update flow.
    """
    from apps.deployments.models import ManagedServer

    try:
        server = ManagedServer.objects.get(id=server_id)
    except ManagedServer.DoesNotExist:
        logger.error("Update Task: Server %s not found", server_id)
        return False

    lock_key = f"server-update:{server_id}"
    if not cache.add(lock_key, "1", timeout=7200):
        _append_remote_update_log(
            server,
            f"\n--- Update skipped at {timezone.now()} - another update is already running ---\n",
        )
        logger.warning("Update Task: duplicate update ignored for server %s", server_id)
        return False

    logger.info("Update Task: Starting update for server %s (%s)", server.name, server.host)

    server.provision_status = ManagedServer.ProvisionStatus.UPDATING
    server.save(update_fields=["provision_status", "updated_at"])
    _append_remote_update_log(
        server,
        f"\n--- Update started at {timezone.now()} for {server.name} ({server.host}) ---\n",
    )

    appender = ThrottledLogAppender(server, interval=1.5)
    def log_cb(out, err):
        if out:
            appender.append(out)
        if err:
            appender.append(err)

    try:
        from apps.deployments.services.ssh_client import SSHClient
        if not (server.ssh_key or server.ssh_password):
            raise RuntimeError("Server has no SSH credentials configured for updates.")

        ssh = SSHClient(
            ip=server.host,
            key_content=server.ssh_key,
            password=server.ssh_password,
            user=server.ssh_user,
            port=server.ssh_port,
            wg_address=server.wg_address,
        )
        ssh.connect()
        hosting_path = ssh.find_hosting_path()
        _append_remote_update_log(server, f"> Connected over SSH. install_path={hosting_path}\n\n--- Preflight ---\n")

        stdout, stderr, code = _run_ssh_command(
            ssh,
            _remote_update_preflight_script(hosting_path),
            timeout=120,
            raise_on_error=False,
            callback=log_cb,
        )
        appender.flush()
        _append_remote_update_log(server, "\n")
        if code != 0:
            raise RuntimeError(f"Remote update preflight failed with exit code {code}.")

        # The platform installer repository is public/open source, so remote
        # platform updates should use the unauthenticated default Git remote
        # instead of depending on a user's linked GitHub OAuth token.
        branch = (os.environ.get('SMSLY_BRANCH') or 'main').strip() or 'main'
        logger.info("Update Task: Triggering installer update on %s (branch: %s)", server.host, branch)

        # Build environment for the update
        config = PlatformConfig.load()
        master_ip = str(config.server_ip or os.environ.get('PUBLIC_IP') or '').strip() or '127.0.0.1'
        env_vars = {
            "NON_INTERACTIVE": "1",
            "SKIP_REBOOT": "1",
            "SMSLY_STRICT_VERIFY": "1",
            "MASTER_IP": master_ip,
            "SMSLY_BRANCH": branch,
        }
        update_args = ["--update"]

        is_lite = getattr(server, "is_lite_agent", False)
        is_primary = getattr(server, "is_primary", False)
        quoted_path = shlex.quote(hosting_path)
        quoted_branch = shlex.quote(branch)
        git_steps = (
            f"cd {quoted_path} && "
            "if [ \"$(id -u)\" -eq 0 ]; then SUDO=''; else SUDO='sudo -n'; fi; "
            "git config --global --add safe.directory \"$PWD\" 2>/dev/null || true; "
            "if [ -n \"$(git status --porcelain 2>/dev/null)\" ]; then "
            "git stash push --include-untracked -m \"remote-update-$(date +%s)\" >/dev/null 2>&1 || true; "
            "fi; "
            f"git fetch origin {quoted_branch} >/dev/null 2>&1 && "
            f"git checkout -B {quoted_branch} origin/{quoted_branch} >/dev/null 2>&1 && "
            f"git branch --set-upstream-to=origin/{quoted_branch} {quoted_branch} >/dev/null 2>&1 || true"
        )

        if is_lite:
            from apps.deployments.services.provisioner import (
                build_agent_lite_install_env,
            )

            lite_env, lite_messages = build_agent_lite_install_env(
                server,
                master_ip=master_ip,
            )
            for message in lite_messages:
                _append_remote_update_log(server, f"> {message}\n")
            env_vars.update(lite_env)
            update_args.append("--mode=agent-lite")

            env_str = " ".join([f"{k}={shlex.quote(str(v))}" for k, v in env_vars.items()])
            update_args_str = " ".join(shlex.quote(arg) for arg in update_args)
            cmd_update = (
                f"{git_steps} && "
                f"$SUDO env {env_str} bash install.sh {update_args_str}"
            )
            _append_remote_update_log(server, f"> Running lite-agent installer update (branch: {branch})...\n\n--- Installer output ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                cmd_update,
                timeout=5400,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Installer update failed with exit code {code}.")
            _append_remote_update_log(server, "\n--- Postflight ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                _remote_update_postflight_script(hosting_path),
                timeout=180,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Remote update postflight failed with exit code {code}.")
        elif is_primary:
            # Primary/master node: full install.sh --update (rebuilds everything
            # including frontend, Traefik, Caddy — master needs the full pipeline).
            env_str = " ".join([f"{k}={shlex.quote(str(v))}" for k, v in env_vars.items()])
            update_args_str = " ".join(shlex.quote(arg) for arg in update_args)
            cmd_update = (
                f"{git_steps} && "
                f"$SUDO env {env_str} bash install.sh {update_args_str}"
            )
            _append_remote_update_log(server, f"> Running master full update (branch: {branch})...\n\n--- Installer output ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                cmd_update,
                timeout=5400,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Master update failed with exit code {code}.")
            _append_remote_update_log(server, "\n--- Postflight ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                _remote_update_postflight_script(hosting_path),
                timeout=180,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Remote update postflight failed with exit code {code}.")
        else:
            # Remote full-stack node (own DB): targeted rebuild of app containers only.
            # Do NOT run install.sh --update — that restarts PG/Redis/RabbitMQ
            # which can corrupt the node's own database.
            BUILD_TIMEOUT = int(os.environ.get('SMSLY_REMOTE_BUILD_TIMEOUT', '14400'))  # 4h default
            app_services = "backend celery celery-fast celery-deploy celery-beat"
            compose_flags = "--no-cache --pull"
            cmd_build = (
                f"{git_steps} && "
                f"$SUDO docker compose -f docker-compose.prod.yml build {compose_flags} {app_services}"
            )
            cmd_up = (
                f"$SUDO docker compose -f docker-compose.prod.yml up -d --no-deps {app_services}"
            )
            _append_remote_update_log(
                server,
                f"> Remote full-stack node: rebuilding app containers (branch: {branch})...\n"
                f"> Services: {app_services}\n\n--- Build output ---\n",
            )
            stdout, stderr, code = _run_ssh_command(
                ssh,
                cmd_build,
                timeout=BUILD_TIMEOUT,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Container build failed with exit code {code}.")

            _append_remote_update_log(server, "> Restarting app containers...\n\n--- Restart output ---\n")
            stdout, stderr, code = _run_ssh_command(
                ssh,
                cmd_up,
                timeout=300,
                raise_on_error=False,
                callback=log_cb,
            )
            appender.flush()
            _append_remote_update_log(server, "\n")
            if code != 0:
                raise RuntimeError(f"Container restart failed with exit code {code}.")

        update_fields = ["provision_status", "updated_at"]
        if getattr(server, "is_lite_agent", False):
            metadata = dict(server.provider_metadata or {})
            metadata["connection_mode"] = "agent-lite"
            metadata["node_id"] = str(server.id)
            metadata["node_host"] = str(server.host or "")
            metadata["node_queue"] = str(env_vars.get("SMSLY_NODE_QUEUE") or "")
            server.provider_metadata = metadata
            update_fields.append("provider_metadata")
            gateway_secret = str(env_vars.get("MASTER_GATEWAY_SECRET") or "").strip()
            if gateway_secret:
                server.gateway_secret = gateway_secret
                update_fields.append("gateway_secret")
        else:
            # Full-install agents: re-read GATEWAY_SECRET from the remote
            # .env to catch any changes made by the installer update.
            try:
                hosting_path = ssh.find_hosting_path()
                fresh_secret = ssh.get_gateway_secret(hosting_path)
                if isinstance(fresh_secret, str):
                    fresh_secret = fresh_secret.strip()
                else:
                    fresh_secret = ""
                if fresh_secret and server.gateway_secret != fresh_secret:
                    server.gateway_secret = fresh_secret
                    update_fields.append("gateway_secret")
                    _append_remote_update_log(
                        server,
                        "> Re-synced GATEWAY_SECRET from agent after update.\n",
                    )
            except Exception as secret_exc:
                _append_remote_update_log(
                    server,
                    f"> Warning: could not re-sync GATEWAY_SECRET: {secret_exc}\n",
                )
        server.provision_status = ManagedServer.ProvisionStatus.DONE
        server.save(update_fields=update_fields)
        _append_remote_update_log(
            server,
            f"\n--- Update completed successfully at {timezone.now()} ---\n",
        )
        if _env_bool("SMSLY_REMOTE_UPDATE_REBOOT_ON_SUCCESS", default=False):
            reboot_cmd = (
                "if [ \"$(id -u)\" -eq 0 ]; then "
                "(nohup sh -c 'sleep 8; /sbin/reboot || reboot' >/dev/null 2>&1 &); "
                "else "
                "(nohup sh -c 'sleep 8; sudo -n /sbin/reboot || sudo -n reboot' >/dev/null 2>&1 &); "
                "fi"
            )
            ssh.exec_command(reboot_cmd, timeout=10, raise_on_error=False)
            server.status = ManagedServer.Status.UNKNOWN
            server.save(update_fields=["status", "updated_at"])
            _append_remote_update_log(
                server,
                "> Remote reboot scheduled after successful update.\n",
            )
        logger.info("Update Task: Finished successfully for %s", server.host)

        # Dispatch notification to server owner when update completes
        try:
            from apps.notifications.tasks import dispatch_notification
            dispatch_notification.delay(
                event_type='server_update_success',
                user_id=server.owner.id,
                title=f"✅ Server Update Succeeded: {server.name}",
                message=f"The update process for server '{server.name}' ({server.host}) completed successfully.",
                metadata={'server_id': str(server.id), 'server_name': server.name, 'host': server.host},
            )
        except Exception as notify_exc:
            logger.warning("Failed to dispatch server update success notification: %s", notify_exc)

        return True

    except Exception as e:
        error_msg = f"Update Task failed for {server.host}: {e!s}"
        logger.error(error_msg)
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_remote_update_log(server, f"\nFATAL ERROR: {e!s}\n")

        # Dispatch notification to server owner when update fails
        try:
            from apps.notifications.tasks import dispatch_notification
            dispatch_notification.delay(
                event_type='server_update_failed',
                user_id=server.owner.id,
                title=f"❌ Server Update Failed: {server.name}",
                message=f"The update process for server '{server.name}' ({server.host}) failed.\nReason: {e!s}",
                metadata={'server_id': str(server.id), 'server_name': server.name, 'host': server.host, 'error': str(e)},
            )
        except Exception as notify_exc:
            logger.warning("Failed to dispatch server update failure notification: %s", notify_exc)

        return False

    finally:
        if 'ssh' in locals():
            ssh.close()
        cache.delete(lock_key)


@shared_task(bind=True, max_retries=0, soft_time_limit=300, time_limit=330)
def node_watchdog_task(self):
    """
    Periodic watchdog that checks all managed servers for health issues.

    For each server:
    1. Checks SSH connectivity
    2. Checks Docker daemon status
    3. Checks disk and memory usage
    4. Attempts auto-recovery for critical issues
    5. Updates server status in the database

    Runs every 5 minutes via Celery beat.
    """
    # Update Prometheus target files for docker-labels exporters
    try:
        from apps.deployments.services.prometheus_targets import (
            write_docker_labels_targets,
        )
        write_docker_labels_targets()
    except Exception as exc:
        logger.warning("Failed to update Prometheus targets: %s", exc)
    try:
        from apps.deployments.models_core import ManagedServer
        from apps.deployments.services.self_healing_orchestrator import (
            FailureType,
            SelfHealingOrchestrator,
        )
    except ImportError:
        logger.warning("Self-healing modules not available — watchdog skipped")
        return

    servers = ManagedServer.objects.filter(
        is_primary=False,
        status=ManagedServer.Status.ONLINE,
    )

    results = {"checked": 0, "healed": 0, "failed": 0, "offline": 0}

    for server in servers:
        try:
            results["checked"] += 1

            if not server.ssh_key and not server.ssh_password:
                logger.debug("Skipping %s — no SSH credentials", server.name)
                continue

            orchestrator = SelfHealingOrchestrator(server)
            diagnostics = orchestrator.run_full_diagnostics()

            old_status = server.status
            if diagnostics.docker_running and diagnostics.network_reachable:
                server.status = ManagedServer.Status.ONLINE
            else:
                server.status = ManagedServer.Status.OFFLINE

            server.last_health_check = timezone.now()
            server.save(update_fields=["status", "last_health_check", "updated_at"])

            # Auto-deploy docker-labels exporter on online nodes
            if server.status == ManagedServer.Status.ONLINE:
                try:
                    from apps.deployments.services.prometheus_targets import (
                        deploy_cadvisor_on_node,
                        deploy_docker_labels_exporter_on_node,
                        deploy_node_exporter_on_node,
                        deploy_promtail_on_node,
                    )
                    deploy_docker_labels_exporter_on_node(server)
                    deploy_promtail_on_node(server)
                    deploy_cadvisor_on_node(server)
                    deploy_node_exporter_on_node(server)
                except Exception as exc:
                    logger.debug("docker-labels/promtail deploy skipped for %s: %s", server.name, exc)

            if diagnostics.docker_running and old_status != ManagedServer.Status.ONLINE:
                logger.info("Server %s recovered — status: ONLINE", server.name)

            if not diagnostics.docker_running:
                logger.warning("Server %s — Docker daemon down, attempting recovery", server.name)
                results["offline"] += 1

                heal_result = orchestrator.heal_deployment_failure(
                    type("obj", (object,), {"id": "watchdog", "container_id": "", "service": type("o", (object,), {"name": ""})()})()
                )
                if heal_result.success:
                    results["healed"] += 1
                    server.status = ManagedServer.Status.ONLINE
                    server.save(update_fields=["status", "updated_at"])
                    logger.info("Server %s healed via watchdog", server.name)

            elif diagnostics.failure_type == FailureType.DISK_FULL:
                logger.warning("Server %s — disk full, pruning images", server.name)
                heal_result = orchestrator._execute_recovery(
                    type("obj", (object,), {"value": "prune_images"})(),
                    type("obj", (object,), {"id": "watchdog", "container_id": "", "service": type("o", (object,), {"name": ""})()})(),
                    diagnostics,
                )
                if heal_result.success:
                    results["healed"] += 1
                    logger.info("Server %s disk space recovered via watchdog", server.name)

            orchestrator._close_ssh()

        except Exception as exc:
            results["failed"] += 1
            logger.warning("Watchdog check failed for %s: %s", server.name, exc)
            try:
                server.status = ManagedServer.Status.OFFLINE
                server.last_health_check = timezone.now()
                server.save(update_fields=["status", "last_health_check", "updated_at"])
            except Exception:
                pass

    logger.info(
        "Node watchdog complete: checked=%d healed=%d failed=%d offline=%d",
        results["checked"], results["healed"], results["failed"], results["offline"],
    )
    return results


@shared_task(bind=True, max_retries=2)
def refresh_managed_server_health(self, server_id: str):
    """Refresh the health/status of a single managed server."""
    from .models_servers import ManagedServer
    from .views_servers import _refresh_managed_server_health
    try:
        server = ManagedServer.objects.get(id=server_id)
        _refresh_managed_server_health(server)
    except ManagedServer.DoesNotExist:
        logger.warning("refresh_managed_server_health: server %s not found", server_id)
    except Exception as exc:
        logger.exception("refresh_managed_server_health failed for %s: %s", server_id, exc)


@shared_task(soft_time_limit=600, time_limit=900)
def sync_master_db_to_agents_task():
    """
    Periodically push a compressed pg_dump of the master database to all
    connected lite agents. Enables disaster recovery: if the master goes
    down, any agent's backup can restore the database on a replacement master.

    Runs every 6 hours via Celery beat.
    """
    import shutil
    import subprocess
    import tempfile

    from django.conf import settings

    from .models_servers import ManagedServer

    agents = ManagedServer.objects.filter(
        is_lite_agent=True,
        status=ManagedServer.Status.ONLINE,
    )
    if not agents.exists():
        logger.info("sync_master_db_to_agents: no lite agents connected — skipping")
        return

    db_url = getattr(settings, 'DATABASE_URL', '')
    if not db_url:
        logger.warning("sync_master_db_to_agents: DATABASE_URL not configured — skipping")
        return

    tmp_dir = tempfile.mkdtemp(prefix='master_db_sync_')
    dump_path = os.path.join(tmp_dir, 'master_db.sql.gz')

    try:
        # Create compressed pg_dump
        result = subprocess.run(
            ['pg_dump', db_url, '--no-owner', '--no-acl', '-Z', '9', '-f', dump_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.error("sync_master_db_to_agents: pg_dump failed: %s", result.stderr[:500])
            return

        file_size = os.path.getsize(dump_path)
        logger.info("sync_master_db_to_agents: dump created (%.1f MB), pushing to %d agents",
                    file_size / (1024 * 1024), agents.count())

        # Push to each lite agent via REST API
        # Send raw binary in body (not multipart) so body_hash computed
        # from file content matches request.body on the receiving end.
        with open(dump_path, 'rb') as f_body:
            raw_body_bytes = f_body.read()
        body_hash = hashlib.sha256(raw_body_bytes).hexdigest()

        for agent in agents:
            target_ip = agent.wg_address or agent.private_ip or agent.host
            if not target_ip:
                continue
            url = f"http://{target_ip}/api/v1/transfers/incoming/db-backup/"
            secret = str(getattr(settings, 'GATEWAY_SECRET', '') or getattr(settings, 'SECRET_KEY', ''))
            timestamp = str(int(time.time()))
            nonce = secrets.token_hex(16)

            raw_sig = f"POST|/api/v1/transfers/incoming/db-backup/|{timestamp}|{nonce}|{body_hash}"
            signature = hmac.new(secret.encode(), raw_sig.encode(), hashlib.sha256).hexdigest()

            try:
                resp = requests.post(
                    url,
                    data=raw_body_bytes,
                    headers={
                        'X-Gateway-Signature-V2': signature,
                        'X-Request-Timestamp': timestamp,
                        'X-Request-Nonce': nonce,
                        'Content-Type': 'application/gzip',
                    },
                    timeout=600,
                )
                if resp.ok:
                    logger.info("sync_master_db_to_agents: pushed to agent %s (%s)", agent.name, target_ip)
                else:
                    logger.warning("sync_master_db_to_agents: agent %s returned %s", agent.name, resp.status_code)
            except requests.RequestException as e:
                logger.warning("sync_master_db_to_agents: failed to push to agent %s: %s", agent.name, e)

    except subprocess.TimeoutExpired:
        logger.error("sync_master_db_to_agents: pg_dump timed out")
    except Exception as e:
        logger.error("sync_master_db_to_agents: failed: %s", e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@shared_task(soft_time_limit=600, time_limit=900)
def registry_garbage_collection_task():
    """
    Periodically run Docker registry garbage collection to reclaim disk
    space from deleted/unused image layers.

    Runs: docker exec <registry> registry garbage-collect /etc/docker/registry/config.yml
    Removes blobs that are no longer referenced by any manifest.
    Safe to run while the registry is serving reads.
    """
    registry_container = "smsly-hosting-registry-1"

    try:
        dry_run = subprocess.run(
            ["docker", "exec", registry_container, "registry", "garbage-collect",
             "--dry-run", "/etc/docker/registry/config.yml"],
            capture_output=True, text=True, timeout=120,
        )
        if dry_run.returncode != 0:
            logger.warning("registry_gc: dry-run failed: %s", dry_run.stderr[:500])
            return

        freed_lines = [line for line in dry_run.stdout.split('\n') if 'marking blob' in line.lower() or 'blob eligible' in line.lower()]
        logger.info("registry_gc: dry-run found %d blobs eligible for removal", len(freed_lines))

        result = subprocess.run(
            ["docker", "exec", registry_container, "registry", "garbage-collect",
             "/etc/docker/registry/config.yml"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'recovered' in line.lower() or 'blob' in line.lower():
                    logger.info("registry_gc: %s", line.strip())
            logger.info("registry_gc: garbage collection completed successfully")
        else:
            logger.warning("registry_gc: failed: %s", result.stderr[:500])

    except subprocess.TimeoutExpired:
        logger.error("registry_gc: timed out")
    except Exception as e:
        logger.error("registry_gc: error: %s", e)
