# pylint: disable=too-many-lines
"""Tasks module."""
import logging
import re
import shlex
import shutil
import tempfile
import subprocess
import os
import json
import time
import zipfile
import secrets
from urllib.parse import unquote, urlparse

import docker
import requests
from celery import shared_task

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.db.models import Sum

from apps.billing.models import UsageRecord, UserSubscription, Invoice, PricingPlan, DailyRevenue, InfrastructureCost
from apps.billing.services.metering import UsageMeter
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
from apps.deployments.models import Service, Deployment, EnvironmentVariable, PlatformConfig
from apps.deployments.models_addons import Addon, Backup
from apps.deployments.models_backup import BackupSchedule, ServiceBackup
from apps.deployments.models_storage import Volume
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.backup_service import BackupService
from apps.deployments.services.pipeline import PipelineManager, PipelineError
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.services.transfer_service import ServerTransferService
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    build_local_source_bundle,
    get_github_oauth_token_for_user,
    update_stage,
)
from services.addon_provisioner import addon_provisioner

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, str(default))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _regenerate_caddyfile():
    """Regenerate and apply the Caddyfile with current service domains.

    Called after successful deployments so new services get Caddy site blocks
    (and therefore SSL certificates) without requiring a manual Settings save.
    """
    try:
        config = PlatformConfig.load()
        from services.caddy_manager import generate_caddyfile, apply_caddyfile
        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            logger.info("Caddyfile regenerated after deployment")
        else:
            logger.warning("Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not regenerate Caddyfile: %s", exc)


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

    from apps.deployments.ai_router import is_ai_router_service

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
        try:
            os.unlink(config_path)
        except OSError:
            pass


def _docker_safe_segment(value: str, fallback: str = "app") -> str:
    """Normalize strings used in Docker image tags and names."""
    slug = re.sub(r"[^a-z0-9_.-]+", "-", str(value or "").lower()).strip("-.")
    if not slug:
        slug = fallback
    return slug[:63]


def _detect_exposed_port(service) -> int | None:
    """Auto-detect port from Docker image EXPOSE directive.

    Inspects the last deployed image for this service. If the image has
    EXPOSE ports, returns the first one. This prevents the common mismatch
    where Dockerfile EXPOSE says 3000 but we default PORT to 8000.
    """
    try:
        last_dep = service.deployments.filter(
            container_id__isnull=False
        ).order_by('-created_at').first()
        if not last_dep or not last_dep.container_id:
            return None

        client = docker.from_env()
        try:
            container = client.containers.get(last_dep.container_id)
            exposed = container.image.attrs.get('Config', {}).get('ExposedPorts', {})
        except docker.errors.NotFound:
            # Container gone, try looking up image directly
            image_tag = last_dep.image_name or ''
            if not image_tag:
                return None
            try:
                img = client.images.get(image_tag)
                exposed = img.attrs.get('Config', {}).get('ExposedPorts', {})
            except docker.errors.ImageNotFound:
                return None

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


def _build_runtime_env(service: Service) -> dict:
    """Assemble runtime env vars with routing domains sourced from Service."""
    env_vars = {env.key: env.value for env in service.env_vars.all()}

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
        elif service.internal_port and int(service.internal_port) != 8000:
            env_vars['PORT'] = str(service.internal_port)
        else:
            detected_port = _detect_exposed_port(service)
            if detected_port:
                env_vars['PORT'] = str(detected_port)
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
    if 'ALLOWED_HOSTS' not in env_vars:
        env_vars['ALLOWED_HOSTS'] = hosts_csv
    env_vars.setdefault('DJANGO_ALLOWED_HOSTS', hosts_csv)
    env_vars.setdefault('MARKETER_ALLOWED_HOSTS', hosts_csv)

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

        env_vars.setdefault('DB_HOST', parsed.hostname)
        env_vars.setdefault('DB_PORT', str(parsed.port or 5432))
        env_vars.setdefault('DB_USER', parsed.username or 'postgres')
        env_vars.setdefault('DB_NAME', parsed.path.lstrip('/') or 'postgres')

        if parsed.password:
            env_vars.setdefault('DB_PASSWORD', parsed.password)
            env_vars.setdefault('MARKETER_DB_PASSWORD', parsed.password)

        # Postgres-specific aliases some frameworks use
        env_vars.setdefault('POSTGRES_HOST', parsed.hostname)
        env_vars.setdefault('POSTGRES_PORT', str(parsed.port or 5432))
        env_vars.setdefault('POSTGRES_USER', parsed.username or 'postgres')
        env_vars.setdefault('POSTGRES_DB', parsed.path.lstrip('/') or 'postgres')
        if parsed.password:
            env_vars.setdefault('POSTGRES_PASSWORD', parsed.password)
    except Exception:
        pass  # Don't block deploy if URL parsing fails


def _smart_derive_redis_vars(env_vars: dict):
    """Parse REDIS_URL into Celery broker/backend vars."""
    redis_url = env_vars.get('REDIS_URL', '')
    if not redis_url:
        return

    try:
        # Celery broker and result backend default to the Redis URL
        env_vars.setdefault('CELERY_BROKER_URL', redis_url)
        env_vars.setdefault('CELERY_RESULT_BACKEND', redis_url)

        # Some apps use numbered Redis databases for separation
        parsed = urlparse(redis_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # If broker is on /0, put result backend on /1
        if not parsed.path or parsed.path == '/' or parsed.path == '/0':
            if env_vars.get('CELERY_BROKER_URL') == redis_url:
                env_vars['CELERY_BROKER_URL'] = f"{base}/0"
            if env_vars.get('CELERY_RESULT_BACKEND') == redis_url:
                env_vars['CELERY_RESULT_BACKEND'] = f"{base}/1"

        # Cache URL alias
        env_vars.setdefault('CACHE_URL', redis_url)
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
            rewrite_database_url,
            resolve_service_url,
            get_sibling_env_value,
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
    if db_name and 'DATABASE_URL' in env_vars:
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
    if 'DATABASE_URL' not in env_vars and 'POSTGRES' in shared_addons:
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
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        from psycopg2 import sql

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

        service = deployment.service
        provider = CloudProvider.objects.get(id=provider_id)

        # 0. Remote Delegation
        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()
        
        # Loop Prevention: If this is already a delegated deployment, handle it locally.
        is_delegated = deployment.source_node is not None
        is_local = is_delegated or (not service.server) or service.server.is_primary or (service.server.host == config.server_ip)

        if not is_local:
            if deployment.remote_deployment_id:
                _resume_remote_deployment(deployment, service.server)
            else:
                _handle_remote_deployment(deployment, service.server, skip_review=skip_review)
            return

        # 1. Build Phase (Pipeline)
        if service.deploy_type == 'GIT':
            manager = PipelineManager(deployment)

            # Skip review for: rollbacks, restarts, webhooks
            if deployment.is_rollback or skip_review:
                image_name = manager.run()
            else:
                # Fresh manual deploy → analysis only, pause for review
                manager.run_analysis_only()
                broadcast_status(deployment)
                return  # Paused at REVIEW → user must approve

        elif service.deploy_type == 'FUNCTION':
            image_name = _build_function(deployment, service)

        elif service.deploy_type == 'DOCKER':
            image_name = service.docker_image

        elif service.deploy_type == 'UPLOAD':
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
        provider = CloudProvider.objects.get(id=provider_id)

        # 0. Remote Delegation
        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()
        
        # Loop Prevention: If this is already a delegated deployment, handle it locally.
        is_delegated = deployment.source_node is not None
        is_local = is_delegated or (not service.server) or service.server.is_primary or (service.server.host == config.server_ip)

        if not is_local:
            if deployment.remote_deployment_id:
                _resume_remote_deployment(deployment, service.server)
            else:
                _handle_remote_deployment(deployment, service.server)
            return

        # Build phase
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
    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
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
        _handle_failure(None, deployment, "Failed to sync service to remote server", "Remote Sync Failure")
        return

    update_stage(deployment, 'Remote Sync', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')

    # 2. Trigger Deploy
    remote_dep_id = orchestrator.trigger_deploy(deployment, remote_svc_id)
    if not remote_dep_id:
        _handle_failure(None, deployment, "Failed to trigger deployment on remote server", "Remote Deploy Failure")
        return

    append_log(deployment, f"🚀 Remote deployment triggered: {remote_dep_id}\n")

    # 3. Polling Loop
    max_retries = 90  # 15 minutes (10s intervals)
    for i in range(max_retries):
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


def _stop_local_service_container(service_name: str):
    """
    Proactively stop and remove any local container on the Master VPS.
    Used during remote delegation to prevent 'ghost' containers.
    """
    try:
        from apps.cloud.docker_client import get_docker_client
        import docker
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


def _handle_remote_deployment(deployment, server, skip_review=False):
    """Delegate deployment to a remote server and poll for status."""
    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
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

    append_log(deployment, f"Delegating deployment to remote server: {server.name} ({server.host})\n")
    update_stage(deployment, 'Remote Sync', 'running')

    remote_svc_id = orchestrator.sync_service(service)
    if not remote_svc_id:
        _handle_failure(None, deployment, "Failed to sync service to remote server", "Remote Sync Failure")
        return

    update_stage(deployment, 'Remote Sync', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')

    remote_dep_id = orchestrator.trigger_deploy(deployment, remote_svc_id, skip_review=skip_review)
    if not remote_dep_id:
        _handle_failure(None, deployment, "Failed to trigger deployment on remote server", "Remote Deploy Failure")
        return

    deployment.remote_deployment_id = remote_dep_id
    deployment.status = Deployment.Status.QUEUED  # Stay queued until follower reports a stage
    deployment.started_at = deployment.started_at or timezone.now()
    deployment.save(update_fields=['remote_deployment_id', 'status', 'started_at', 'updated_at'])
    append_log(deployment, f"Remote deployment triggered: {remote_dep_id}\n")
    _poll_remote_deployment(deployment, orchestrator, remote_dep_id)


def _resume_remote_deployment(deployment, server):
    """Approve/resume an existing remote deployment and keep polling it."""
    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
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
        _handle_failure(None, deployment, "Failed to approve remote deployment", "Remote Approval Failure")
        return

    update_stage(deployment, 'Remote Approval', 'success')
    update_stage(deployment, 'Remote Deploy', 'running')
    _poll_remote_deployment(deployment, orchestrator, remote_dep_id)


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


def _poll_remote_deployment(deployment, orchestrator, remote_dep_id):
    """Poll a delegated deployment until it reaches REVIEW or a terminal state."""
    max_retries = 90  # 15 minutes (10s intervals)
    for _ in range(max_retries):
        time.sleep(10)
        remote_status = orchestrator.poll_deployment(remote_dep_id)
        if not remote_status:
            continue

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
            Deployment.Status.TRAFFIC_SHIFTING,
            Deployment.Status.MONITORING,
            Deployment.Status.STAGED,
        ) and deployment.status != status:
            deployment.status = status
            deployment.save(update_fields=['status', 'updated_at'])
            broadcast_status(deployment)

        if status == Deployment.Status.ACTIVE:
            deployment.status = Deployment.Status.ACTIVE
            deployment.finished_at = timezone.now()
            deployment.save(update_fields=['status', 'finished_at'])
            update_stage(deployment, 'Remote Deploy', 'success')
            broadcast_status(deployment)
            append_log(deployment, "Remote deployment successful.\n")
            return

        if status in (Deployment.Status.FAILED, Deployment.Status.BUILD_FAILED, Deployment.Status.CANCELLED):
            _handle_failure(None, deployment, f"Remote deployment failed with status: {status}", "Remote Execution Failure")
            return

    _handle_failure(None, deployment, "Remote deployment timed out", "Remote Timeout")


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
        if registry:
            return NixpacksBuilder.push_image(tag, registry)
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
        if service.buildpack == "DOCKER" and os.path.isfile(dockerfile_path):
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
            append_log(deployment, "Building uploaded source with Nixpacks...\n")
            NixpacksBuilder.build_image(
                source_dir=source_dir,
                image_name=image_name,
                env_vars=env_map,
            )

        registry = getattr(settings, "CONTAINER_REGISTRY_URL", None)
        if registry:
            append_log(deployment, f"Pushing uploaded image to {registry}...\n")
            image_name = NixpacksBuilder.push_image(image_name, registry)
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


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


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
            45,
            minimum=10,
        )
    return _env_int("LOCAL_ROUTE_READY_TIMEOUT_SECONDS", 30, minimum=10)


def _local_container_timeout_seconds(service: Service) -> int:
    if _is_low_resource_service(service):
        return _env_int(
            "LOCAL_CONTAINER_HEALTH_TIMEOUT_LOW_RESOURCE_SECONDS",
            420,
            minimum=30,
        )
    return _env_int("LOCAL_CONTAINER_HEALTH_TIMEOUT_SECONDS", 240, minimum=30)


def _wait_for_local_container_healthy(
    deployment,
    container_id: str,
    timeout_seconds: int = 180,
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

        # No Docker healthcheck configured; consider running container ready.
        if status == "running" and not health:
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container running without healthcheck ({last_state}).\n",
            )
            return True

        time.sleep(poll_seconds)

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

    # Probe through both internal ingress and the actual public hostname.
    probe_candidates = []

    def _add_probe(base_url: str, headers=None, verify=True):
        normalized = (base_url or "").rstrip("/")
        if not normalized:
            return
        probe_candidates.append(
            {
                "base_url": normalized,
                "headers": headers or {},
                "verify": verify,
            }
        )

    configured = os.environ.get("TRAEFIK_INTERNAL_URL", "").strip()
    if configured:
        _add_probe(configured, headers={"Host": host}, verify=False)
    _add_probe("http://traefik:80", headers={"Host": host}, verify=False)
    _add_probe("http://127.0.0.1:8081", headers={"Host": host}, verify=False)
    _add_probe("http://localhost:8081", headers={"Host": host}, verify=False)
    _add_probe(f"https://{host}", verify=True)
    _add_probe(f"http://{host}", verify=True)

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
                        headers=probe["headers"],
                        timeout=8,
                        verify=probe["verify"],
                        allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    last_error = f"{url}: {exc}"
                    continue

                if response.status_code >= 500:
                    last_error = f"{url}: HTTP {response.status_code}"
                    continue

                # Traefik can briefly return default 404 while labels propagate.
                if _is_traefik_not_ready(response):
                    last_error = f"{url}: Traefik route not ready yet"
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
            append_log(deployment, f"⚠️ Warning: Registry pull failed for {image_name}. "
                                   "Attempting deployment using local cache...\n")

        env_vars = _build_runtime_env(service)

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
            replicas=service.min_replicas,
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
            if service.is_public:
                route_timeout = _local_route_timeout_seconds(service)
                route_ready = _wait_for_local_route_ready(
                    deployment, service, timeout_seconds=route_timeout,
                )
                if not route_ready:
                    append_log(
                        deployment,
                        "[ROUTE-CHECK] WARNING: Route not ready before STAGED. "
                        "Will recheck at promotion.\n",
                    )
            _run_managed_image_post_deploy_hooks(
                deployment,
                service,
                resource.resource_id,
            )

        # Container is live with Traefik labels - mark ACTIVE.
        # Local adapter may internally perform staged blue-green promotion
        # before returning the final live container ID.
        deployment.status = Deployment.Status.ACTIVE
        deployment.container_id = resource.resource_id
        deployment.save()  # full save() triggers model hook that cancels other ACTIVE deploys

        update_stage(
            deployment,
            'Deploy',
            'done',
            (timezone.now() - start).total_seconds()
        )
        broadcast_status(deployment)
        append_log(
            deployment,
            f"[DEPLOY] ✅ Container live with Traefik routing enabled.\n"
            f"Domain should be accessible immediately.\n"
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
        deployment.container_id = green_id
        deployment.status = Deployment.Status.ACTIVE
        deployment.finished_at = timezone.now()
        deployment.save(update_fields=['status', 'container_id', 'finished_at'])
        broadcast_status(deployment)
        _regenerate_caddyfile()
        return

    # Perform atomic cutover
    promoted_id = adapter.promote_container(service.name, green_id)

    deployment.container_id = promoted_id
    deployment.status = Deployment.Status.ACTIVE
    deployment.finished_at = timezone.now()
    deployment.save(update_fields=['status', 'container_id', 'finished_at'])

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


@shared_task(bind=True, max_retries=0, soft_time_limit=300, time_limit=360)
def auto_promote_task(self, deployment_id: str, provider_id: str):
    """
    Auto-promote a STAGED deployment after bake period.

    Scheduled with countdown=1800 (30 minutes) when deployment enters STAGED.
    Only promotes if still in STAGED status (user may have already promoted
    manually or the deployment may have been cancelled/failed).
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)
    except Deployment.DoesNotExist:
        return

    # Only promote if still STAGED (not already promoted or failed)
    if deployment.status != 'STAGED':
        logger.info(
            "Auto-promote skipped for %s: status is %s (not STAGED)",
            deployment_id, deployment.status,
        )
        return

    try:
        provider = CloudProvider.objects.get(id=provider_id)
    except CloudProvider.DoesNotExist:
        logger.error("Auto-promote: provider %s not found", provider_id)
        return

    try:
        append_log(deployment, "\n⏰ Bake period complete. Auto-promoting...\n")
        _do_promote(deployment, provider)
    except Exception as e:
        logger.error("Auto-promote failed for %s: %s", deployment_id, e)
        append_log(deployment, f"\n❌ Auto-promote failed: {e}\n")
        deployment.status = 'FAILED'
        deployment.finished_at = timezone.now()
        deployment.build_logs += f"\n--- Auto-Promote Failure ---\n{str(e)}\n"
        deployment.save()
        broadcast_status(deployment)


@shared_task(bind=True, max_retries=0, soft_time_limit=300, time_limit=360)
def promote_deployment_task(self, deployment_id: str, provider_id: str):
    """
    Manually promote a STAGED deployment (triggered by 'Promote Now' button).
    Immediate cutover — no bake wait.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)
    except Deployment.DoesNotExist:
        return

    if deployment.status != 'STAGED':
        logger.warning(
            "Manual promote skipped for %s: status is %s",
            deployment_id, deployment.status,
        )
        return

    try:
        provider = CloudProvider.objects.get(id=provider_id)
    except CloudProvider.DoesNotExist:
        logger.error("Manual promote: provider %s not found", provider_id)
        return

    try:
        append_log(deployment, "\n🚀 Manual promotion triggered (Promote Now)...\n")
        _do_promote(deployment, provider)
    except Exception as e:
        logger.error("Manual promote failed for %s: %s", deployment_id, e)
        append_log(deployment, f"\n❌ Manual promote failed: {e}\n")
        deployment.status = 'FAILED'
        deployment.finished_at = timezone.now()
        deployment.build_logs += f"\n--- Manual Promote Failure ---\n{str(e)}\n"
        deployment.save()
        broadcast_status(deployment)


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
        append_log(deployment, "✅ Container healthy after 30s monitoring.\n")
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
        smart_deploy_task.delay(
            deployment_id=str(new_deployment.id), provider_id=str(provider.id), skip_review=True
        )
        return

    # Step 2: No pattern match → escalate to AI models
    _escalate_to_ai(deployment, service, container_logs)

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
                        deployment.build_logs += f"\n🧹 Cleaned up orphaned container resources.\n"
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

    # Never auto-retry failed deployments.
    # Build failures are deterministic and system failures should be
    # investigated, not blindly retried. Users can manually redeploy.
    logger.error("Deployment failed (%s), not retrying: %s", reason, error_msg)
    return


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
    import json
    template_path = os.path.join(
        settings.BASE_DIR, 'apps/deployments/fixtures/templates.json'
    )
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            templates = json.load(f)
        template = next((t for t in templates if t.get('id') == template_id), None)
    except Exception as exc: # pylint: disable=broad-exception-caught
        print(f"DEBUG: Exception reading template JSON: {exc}")
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
                _, url = addon_provisioner.provision(addon)
                addon.connection_url = url
                addon.status = Addon.Status.ACTIVE
                addon.save()
            except Exception as e:
                logger.error(f"Failed to provision {addon_type} for template: {e}")
                addon.status = Addon.Status.FAILED
                addon.save()
                return # Stop deploy
        
        addon_urls[addon_type] = addon.connection_url
        
        # Inject Env (legacy/direct injection)
        key_map = {
            'POSTGRES': 'DATABASE_URL',
            'REDIS': 'REDIS_URL',
            'MONGODB': 'MONGODB_URI',
            'ELASTICSEARCH': 'ELASTICSEARCH_URL',
        }
        key = key_map.get(addon_type, f"{addon_type}_URL")
        EnvironmentVariable.objects.update_or_create(
            service=service, key=key,
            defaults={'value': addon.connection_url, 'is_secret': True}
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
        # System Environment Overrides & Defaults
        default_ai_senate = os.environ.get('AI_SENATE_URL') or 'http://ollama:11434'
        v = v.replace('${AI_SENATE_URL}', default_ai_senate)
        v = v.replace('${LITELLM_MASTER_KEY}', os.environ.get('LITELLM_MASTER_KEY', ''))
        v = v.replace('${OLLAMA_BASE_URL}', os.environ.get('OLLAMA_BASE_URL', 'http://ollama:11434'))
        v = v.replace('${OLLAMA_MODEL}', os.environ.get('OLLAMA_MODEL', 'llama3'))
        v = v.replace('${AI_ROUTER_API_BASE}', os.environ.get('AI_ROUTER_API_BASE', DEFAULT_AI_ROUTER_API_BASE))
        v = v.replace('${AI_ROUTER_UI_BASE}', os.environ.get('AI_ROUTER_UI_BASE', DEFAULT_AI_ROUTER_UI_BASE))
        v = v.replace('${AI_ROUTER_BRAID_ALIAS}', os.environ.get('AI_ROUTER_BRAID_ALIAS', DEFAULT_BRAID_ALIAS))

        return v

    if template and 'env_vars' in template:
        env_vars = template.get('env_vars') or []
        if isinstance(env_vars, list):
            for item in env_vars:
                if not isinstance(item, dict): continue
                key = str(item.get('key') or '').strip()
                if not key: continue
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

    # One-Click AI Router + Ollama auto-deployment
    if provider and template and template.get('id') == 'ai-router':
        import secrets
        import re
        def slugify(value: str) -> str:
            value = (value or 'service').lower()
            value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')
            return (value[:48] or 'service')

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

            # trigger smart deploy for the companion
            c_deployment = Deployment.objects.create(
                service=c_service,
                status='QUEUED',
                commit_hash='template',
                commit_message=f"Auto-companion Template: {c_template_id}"
            )
            smart_deploy_task.delay(deployment_id=str(c_deployment.id), provider_id=str(provider.id))

        # Automatically update the AI_ROUTER_SELECTED_SERVICE_IDS on the router before deploying it
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
            except Exception as e:
                pass  # Fail gracefully if auto-link fails

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
    try:
        addon = Addon.objects.get(id=addon_id)
        cid, url = addon_provisioner.provision(addon)
        addon.connection_url = url
        addon.status = Addon.Status.ACTIVE
        addon.coolify_uuid = cid
        addon.save()

        # If public domain is assigned, regenerate Caddy configuration
        if addon.public_domain:
            try:
                from .models import PlatformConfig
                from services.caddy_manager import generate_caddyfile, apply_caddyfile
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
            addon_provisioner.deprovision(addon.coolify_uuid, container_name)
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

@shared_task(bind=True, soft_time_limit=3600, time_limit=3900)
def create_service_backup_task(self, service_id, backup_type='MANUAL', backup_id=None):
    backup_service = BackupService()
    backup_service.backup_service(service_id, backup_id=backup_id, backup_type=backup_type)

@shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
def create_server_backup_task(self, backup_id=None):
    backup_service = BackupService()
    backup_service.backup_server(backup_id=backup_id)

@shared_task(bind=True, soft_time_limit=3600)
def restore_service_backup_task(self, backup_id, target_service_id=None, requesting_user_id=None):
    backup_service = BackupService()
    backup_service.restore_service(
        backup_id,
        target_service_id=target_service_id,
        requesting_user_id=requesting_user_id,
    )

@shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
def restore_server_backup_task(self, backup_id):
    backup_service = BackupService()
    backup_service.restore_server(backup_id=backup_id)

@shared_task
def cleanup_old_backups_task():
    """Delete backups older than retention_days per schedule."""
    from datetime import timedelta

    schedules = BackupSchedule.objects.filter(enabled=True)
    for schedule in schedules:
        if schedule.service:
            # Service level
            cutoff = timezone.now() - timedelta(days=schedule.retention_days)
            old_backups = ServiceBackup.objects.filter(
                service=schedule.service,
                created_at__lt=cutoff,
                status='COMPLETED'
            ).order_by('-created_at')

            # Keep at least the latest 1 valid backup, regardless of age
            all_valid_backups = ServiceBackup.objects.filter(
                service=schedule.service,
                status='COMPLETED'
            ).order_by('-created_at')

            if all_valid_backups.count() <= 1:
                old_backups = []
            elif all_valid_backups.first() in old_backups:
                old_backups = old_backups.exclude(id=all_valid_backups.first().id)

            for backup in old_backups:
                # Delete file
                if backup.file_path and os.path.exists(backup.file_path):
                    try:
                        os.remove(backup.file_path)
                    except OSError as e:
                        logger.warning(f"Error deleting backup file {backup.file_path}: {e}")
                backup.delete()

@shared_task(bind=True, soft_time_limit=7200, time_limit=7500)
def execute_server_transfer_task(self, transfer_id):
    from .models_transfer import ServerTransfer as TransferModel
    from apps.deployments.services.transfer_service import ServerTransferService

    transfer = TransferModel.objects.get(id=transfer_id)
    engine = ServerTransferService(transfer)
    engine.execute()


@shared_task(bind=True)
def rollback_transfer_task(self, transfer_id):
    from .models_transfer import ServerTransfer as TransferModel
    from apps.deployments.services.transfer_service import ServerTransferService

    transfer = TransferModel.objects.get(id=transfer_id)
    engine = ServerTransferService(transfer)
    engine.rollback()


@shared_task(bind=True, max_retries=0)
def platform_update_task(self, update_id: str):
    """Execute platform update in background."""
    from .models_updates import PlatformUpdate
    from services.platform_updater import perform_update

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    perform_update(update)


@shared_task(bind=True, max_retries=0)
def platform_rollback_task(self, update_id: str):
    """Execute platform rollback in background (avoids blocking the request thread)."""
    from .models_updates import PlatformUpdate
    from services.platform_updater import _rollback

    try:
        update = PlatformUpdate.objects.get(id=update_id)
    except PlatformUpdate.DoesNotExist:
        return

    _rollback(update)


def _clear_directory_contents(path: str) -> dict:
    """Clear direct children of a known cache directory."""
    root = os.path.abspath(path)
    if root in {"/", "/app", "/opt", "/opt/smsly-hosting"}:
        raise ValueError(f"Refusing to clear unsafe directory: {root}")

    result = {"path": root, "removed": 0, "missing": False, "errors": []}
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
    active_service_ids = set(
        str(value)
        for value in Service.objects.exclude(status__in=["DELETED", "DELETION_PENDING"]).values_list("id", flat=True)
    )
    active_service_names = set(
        str(value)
        for value in Service.objects.exclude(status__in=["DELETED", "DELETION_PENDING"]).values_list("name", flat=True)
    )
    active_addon_ids = set(
        str(value)
        for value in Addon.objects.exclude(status="DELETED").values_list("id", flat=True)
    )

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

    image_prune = {}
    try:
        image_prune = client.images.prune(filters={"dangling": True}) or {}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to prune dangling images: %s", exc)
        errors.append({"name": "dangling-images", "error": str(exc)})

    cache_results = [
        _clear_directory_contents("/app/repo_cache"),
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
            from apps.deployments.models import PlatformConfig
            from services.caddy_manager import generate_caddyfile, apply_caddyfile

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
            from .tasks import platform_update_task

            # Check if an update is already in progress
            in_progress = PlatformUpdate.objects.filter(
                status__in=['PENDING', 'PULLING', 'BACKING_UP', 'RESTARTING', 'HEALTH_CHECK', 'MIGRATING']
            ).exists()

            if in_progress:
                return {
                    "status": "error",
                    "message": "A platform update is already in progress.",
                }

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
    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
    
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        return

    success = False
    
    # 1. Handle remote server cleanup if applicable
    if service.server and not service.server.is_primary:
        try:
            logger.info("Decommissioning service %s on remote node %s", service.name, service.server.host)
            remote = RemoteOrchestrator(service.server)
            # Find the remote service ID (matching by name is the most reliable if ID not stored)
            success = remote.delete_service(str(service.id))
            
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
        
        # 3. Resilience: If force=True, we proceed regardless of resource cleanup success.
        # This ensures the DB record is purged when the user explicitly requests a force-delete.
        if force:
            logger.info("Force-purging service %s from database after best-effort cleanup.", service.name)
            success = True
        elif not success and not service.server and not orchestrator.docker_client:
            logger.warning("Docker client unavailable for service %s. Forcing database-only deletion.", service.name)
            success = True

    if success:
        service.delete()
    else:
        service.status = Service.Status.DELETION_FAILED
        service.deletion_error = "Failed to remove some runtime resources. If this node is unassigned or unreachable, use 'Retry' or manual DB cleanup."
        service.save(update_fields=['status', 'deletion_error'])


@shared_task(bind=True, max_retries=3)
def delete_addon_task(self, addon_id: str):
    """Async reliable deletion of an Addon"""
    from apps.deployments.models_addons import Addon
    from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
    try:
        addon = Addon.objects.get(id=addon_id)
    except Addon.DoesNotExist:
        return

    orchestrator = DeletionOrchestrator()
    success = orchestrator.delete_addon_resources(addon)

    # Resilience: If local docker client is missing but addon has no server (or is unassigned), 
    # allow DB deletion to proceed.
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
    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
    
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


@shared_task(name="apps.deployments.tasks.update_remote_server_task")
def update_remote_server_task(server_id: str):
    """
    SSH into a remote server and trigger a git pull + docker compose restart.
    """
    from apps.deployments.models import ManagedServer
    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator

    try:
        server = ManagedServer.objects.get(id=server_id)
    except ManagedServer.DoesNotExist:
        logger.error("Update Task: Server %s not found", server_id)
        return False

    logger.info("Update Task: Starting update for server %s (%s)", server.name, server.host)
    
    # Track progress in provision_logs
    server.provision_status = ManagedServer.ProvisionStatus.UPDATING
    server.provision_logs = (server.provision_logs or "") + f"\n--- Update started at {timezone.now()} ---\n"
    server.save(update_fields=["provision_status", "provision_logs"])

    try:
        from apps.deployments.services.ssh_client import SSHClient
        ssh = SSHClient(
            ip=server.host,
            key_content=server.ssh_key,
            password=server.ssh_password,
            user=server.ssh_user,
            port=server.ssh_port
        )
        
        # 1. Pull latest code
        # Resolve GitHub token for the server owner to handle private repos
        github_token = get_github_oauth_token_for_user(server.owner)
        auth_url = None
        if github_token:
            from urllib.parse import quote
            encoded = quote(github_token, safe="")
            auth_url = f"https://x-access-token:{encoded}@github.com/SMSLYCLOUD/smsly-hosting.git"

        branch = os.environ.get('SMSLY_BRANCH', 'main')
        logger.info("Update Task: Triggering installer update on %s (branch: %s)", server.host, branch)
        
        # Build environment for the update
        master_ip = os.environ.get('PUBLIC_IP') or '127.0.0.1'
        env_vars = {
            "NON_INTERACTIVE": "1",
            "SKIP_SCREEN": "1",
            "MASTER_IP": master_ip,
            "SMSLY_BRANCH": branch,
        }
        
        if auth_url:
            env_vars["SMSLY_GIT_REMOTE"] = auth_url

        env_str = " ".join([f"{k}='{v}'" for k, v in env_vars.items()])
        cmd_update = f"cd /opt/smsly-hosting && sudo {env_str} bash install.sh --update"
        
        server.provision_logs += f"> Running installer update (branch: {branch})...\n"
        server.save(update_fields=["provision_logs"])
        
        stdout, stderr, code = ssh.exec_command(cmd_update, raise_on_error=False)
        server.provision_logs += stdout + stderr
        server.save(update_fields=["provision_logs"])
        
        if code != 0:
            logger.error("Update Task: Installer update failed on %s", server.host)
            return False

        server.provision_status = ManagedServer.ProvisionStatus.DONE
        server.provision_logs += f"\n--- Update completed successfully at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs"])
        logger.info("Update Task: Finished successfully for %s", server.host)
        return True

    except Exception as e:
        error_msg = f"Update Task failed for {server.host}: {str(e)}"
        logger.error(error_msg)
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.provision_logs += f"\nFATAL ERROR: {str(e)}\n"
        server.save(update_fields=["provision_status", "provision_logs"])
        return False

    finally:
        if 'ssh' in locals():
            ssh.close()
