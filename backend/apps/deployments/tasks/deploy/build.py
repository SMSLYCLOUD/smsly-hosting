"""Build and environment helper functions."""
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from contextlib import suppress
from urllib.parse import unquote, urlparse

import docker
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.cloud.services.builder import NixpacksBuilder
from apps.cloud.services.function_provisioner import FunctionProvisioner
from apps.deployments.services.ai_router import (
    generate_ai_router_proxy_config,
    get_ollama_model_name,
    is_ai_router_service,
    is_ollama_service,
)
from apps.deployments.models import (
    Deployment,
    PlatformConfig,
    Service,
)
from apps.deployments.models.addons import Addon
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    is_deployment_local,
)

from .helpers import _env_bool, _env_int
from .health import _wait_for_local_container_healthy

try:
    from apps.intelligence.models import AIProviderSettings as _AIProviderSettings
except (ImportError, RuntimeError):
    _AIProviderSettings = None
AIProviderSettings = _AIProviderSettings

logger = logging.getLogger(__name__)
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
        from apps.deployments.services.env_resolver import resolve_shortcodes
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
    # Always overwrite: addon URLs may change on re-provision (new password,
    # new container). Using setdefault would keep stale URLs from prior deploys.
    try:
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

    # Always overwrite ALLOWED_HOSTS variants — these are platform-managed
    # domain vars that must reflect the current domains regardless of what
    # the AI Senate or manifest resolver may have set.
    env_vars['ALLOWED_HOSTS'] = hosts_csv
    env_vars['DJANGO_ALLOWED_HOSTS'] = hosts_csv
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
        from apps.deployments.services.ecosystem_graph import (
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
        from urllib.parse import urlparse

        import psycopg2
        from psycopg2 import sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        parsed = urlparse(base_url)
        if not parsed.hostname or parsed.hostname in ('localhost', '127.0.0.1', '0.0.0.0'):
            db_host = os.environ.get('DB_HOST', os.environ.get('DATABASE_HOST', 'db'))
            base_url = base_url.replace(f'@{parsed.hostname or ""}:', f'@{db_host}:')
            if '@' not in base_url:
                base_url = base_url.replace('://', f'://{db_host}:5432/', 1)

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


