import logging

logger = logging.getLogger(__name__)
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

_PROPAGATED_SECRETS = {
    'INTERNAL_API_SECRET',
    'GATEWAY_SECRET',
    'JWT_SECRET',
}

_SERVICE_REDIS_DB = {
    'smsly-helper':     1,
    'smsly-marketer':   4,
}

import logging
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import urlparse

import docker
import requests

from apps.deployments.models import (
    Service,
)
from apps.deployments.models_addons import Addon
from apps.deployments.services.tls_verify import should_verify
from apps.deployments.utils import (
    append_log,
)

from .tasks_utils import _env_bool, _env_int


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
        if isinstance(val, str) and re.search(r"\{\{.*?\}\}", val):
            logger.warning(
                "[PLACEHOLDER] Skipping unresoloved placeholder %s=%s for service %s",
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
    from .tasks_deploy_remote import _is_traefik_not_ready, _route_misroute_reason

    host = (service.public_domain or "").strip()
    if not host:
        return True

    # Probe through the public edge first, then the raw Traefik ingress. The
    # direct Traefik probe is useful during DNS propagation, but it must not
    # mask a Caddy misroute that serves the platform homepage.
    probe_candidates: list[dict[str, Any]] = []

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
