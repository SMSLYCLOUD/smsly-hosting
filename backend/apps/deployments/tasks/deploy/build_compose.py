from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from django.core.cache import cache
from django.utils import timezone

from apps.deployments.models import Deployment, PlatformConfig, Service
from apps.deployments.models.addons import Addon
from apps.deployments.utils import append_log, broadcast_status

from .build_docker import _detect_exposed_port
from .helpers import _env_bool, _env_int

logger = logging.getLogger(__name__)
def fleet_build_lock(deployment):
    if not _env_bool("SMSLY_ENABLE_FLEET_BUILD_LOCK", False):
        append_log(deployment, "🚀 Build starting...\n")
        yield
        return

    try:
        config = PlatformConfig.load()
    except Exception:
        yield
        return

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
        except Exception as exc:
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
        deployment_id = str(deployment.id)
        if cache.add(lock_key, deployment_id, timeout=lock_timeout):
            acquired = True
            cache.set(heartbeat_key, _heartbeat_payload(deployment_id), timeout=lock_timeout)
            break

        current_owner = _normalize_cache_value(cache.get(lock_key))
        if not current_owner:
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
        if _normalize_cache_value(cache.get(lock_key)) == str(deployment.id):
            cache.delete(lock_key)
            cache.delete(heartbeat_key)
            if hasattr(fleet_build_lock, "_attempt_count"):
                delattr(fleet_build_lock, "_attempt_count")

def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _is_legacy_default_healthcheck(service: Service) -> bool:
    return (
        (service.health_check_path or "").strip() == "/health"
        and service.health_check_port in (None, 0)
        and _coerce_int(service.health_check_interval, 60) == 60
        and _coerce_int(service.health_check_timeout, 15) == 15
        and _coerce_int(service.health_check_retries, 8) == 8
    )

def _build_platform_healthcheck(service: Service, env_vars: dict) -> dict | None:
    path = (service.health_check_path or "").strip()
    if not path:
        return None

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
            except Exception as exc:
                logger.debug("Base64 ciphertext check failed: %s", exc)
        return False

    envs = list(service.env_vars.all())
    locked_keys = {env.key for env in envs if env.is_locked}

    env_vars = {}
    for env in envs:
        val = env.value
        if _is_ciphertext(val):
            logger.warning(
                "[DB-ENCRYPT] Skipping ciphertext env var %s for service %s at runtime injection",
                env.key, service.name,
            )
            continue
        if isinstance(val, str) and re.search(r"\{\{.*?\}\}", val):
            logger.warning(
                "[PLACEHOLDER] Skipping unresolved placeholder %s=%s for service %s "
                "at runtime injection — addon may not be provisioned yet.",
                env.key, val, service.name,
            )
            continue
        env_vars[env.key] = val

    try:
        from apps.deployments.services.env_resolver import resolve_shortcodes
        for key, value in env_vars.items():
            env_vars[key] = resolve_shortcodes(str(service.id), value)
    except Exception as e:
        logger.warning(f"Failed to resolve shortcodes for service {service.name}: {e}")

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

    if 'HOSTNAME' not in locked_keys:
        env_vars.setdefault('HOSTNAME', '0.0.0.0')

    if not env_vars.get('SECRET_KEY') and not env_vars.get('DJANGO_SECRET_KEY'):
        env_vars['SECRET_KEY'] = secrets.token_urlsafe(50)

    try:
        if not env_vars.get('FERNET_KEY'):
            from cryptography.fernet import Fernet
            env_vars['FERNET_KEY'] = Fernet.generate_key().decode()
    except Exception as exc:
        logger.debug("Fernet key generation skipped: %s", exc)

    fallback_if_blank = {
        'ADMIN_EMAIL': 'admin@example.com',
        'ADMIN_USERNAME': 'admin',
        'OPS_HEALTH_TOKEN': secrets.token_urlsafe(16),
    }
    for k, v in fallback_if_blank.items():
        if not str(env_vars.get(k, '')).strip():
            env_vars[k] = v

    try:
        from apps.addons.services.addon_provisioner import AddonProvisioner
        for addon in Addon.objects.filter(service=service, status='ACTIVE'):
            env_key = AddonProvisioner.ENV_KEY_MAP.get(addon.addon_type)
            if env_key and addon.connection_url:
                env_vars[env_key] = addon.connection_url
                if addon.addon_type == 'QDRANT':
                    parsed = urlparse(addon.connection_url)
                    env_vars['QDRANT_HOST'] = parsed.hostname or 'localhost'
                    env_vars['QDRANT_PORT'] = str(parsed.port or 6333)
    except Exception as exc:
        logger.debug("Addon env var injection skipped: %s", exc)

    _link_ecosystem(service, env_vars)

    _smart_derive_database_vars(env_vars)
    _smart_derive_redis_vars(env_vars)

    all_hosts = ['localhost', '127.0.0.1', '0.0.0.0']
    if service.public_domain and not service.public_domain_hidden:
        env_vars['PUBLIC_DOMAIN'] = service.public_domain
        all_hosts.append(service.public_domain)
    for d in (service.custom_domains or []):
        if isinstance(d, str) and d.strip():
            all_hosts.append(d.strip())
    hosts_csv = ','.join(all_hosts)

    env_vars['ALLOWED_HOSTS'] = hosts_csv
    env_vars['DJANGO_ALLOWED_HOSTS'] = hosts_csv
    env_vars['MARKETER_ALLOWED_HOSTS'] = hosts_csv

    if service.public_domain:

        port = env_vars.get('PORT', '8000')
        env_vars.setdefault('API_INTERNAL_URL', f'http://127.0.0.1:{port}')

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

    try:
        from .services.infisical import get_infisical_client, get_or_create_workspace, inject_infisical_env_for_service
        _client = get_infisical_client()
        if _client is not None:
            _ws_id = get_or_create_workspace(_client)
            if _ws_id:
                infisical_vars = inject_infisical_env_for_service(str(service.id), _client, _ws_id)
                env_vars.update(infisical_vars)
    except Exception as exc:
        logger.debug("Infisical env injection skipped: %s", exc)

    return env_vars

def _smart_derive_database_vars(env_vars: dict):
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

        env_vars['POSTGRES_HOST'] = parsed.hostname
        env_vars['POSTGRES_PORT'] = str(parsed.port or 5432)
        env_vars['POSTGRES_USER'] = parsed.username or 'postgres'
        env_vars['POSTGRES_DB'] = parsed.path.lstrip('/') or 'postgres'
        if parsed.password:
            env_vars['POSTGRES_PASSWORD'] = parsed.password
    except Exception as exc:
        logger.debug("URL parsing failed for DATABASE_URL: %s", exc)

def _smart_derive_redis_vars(env_vars: dict):
    redis_url = env_vars.get('REDIS_URL', '')
    if not redis_url:
        return

    try:
        env_vars['CELERY_BROKER_URL'] = redis_url
        env_vars['CELERY_RESULT_BACKEND'] = redis_url

        parsed = urlparse(redis_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        if not parsed.path or parsed.path in {'/', '/0'}:
            if env_vars.get('CELERY_BROKER_URL') == redis_url:
                env_vars['CELERY_BROKER_URL'] = f"{base}/0"
            if env_vars.get('CELERY_RESULT_BACKEND') == redis_url:
                env_vars['CELERY_RESULT_BACKEND'] = f"{base}/1"

        env_vars['CACHE_URL'] = redis_url
    except Exception as exc:
        logger.debug("URL parsing failed for REDIS_URL: %s", exc)

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

_SERVICE_REDIS_DB = {
    'smsly-helper':     1,
    'smsly-marketer':   4,
}

def _link_ecosystem(service: Service, env_vars: dict) -> None:
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

    if not svc_name.startswith('preview-'):
        for env_key, match_patterns in _SERVICE_URL_PATTERNS.items():
            if env_key in env_vars:
                continue

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
                    break

    if not svc_name.startswith('preview-'):
        for secret_key in _PROPAGATED_SECRETS:
            if secret_key in env_vars:
                continue

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

    redis_url = env_vars.get('REDIS_URL', '')
    if redis_url:
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
    try:
        last_deploy = service.deployments.order_by('-created_at').first()
        if last_deploy and isinstance(last_deploy.analysis_result, dict):
            db_name = last_deploy.analysis_result.get('database_name')
            if db_name:
                return db_name
    except Exception as exc:
        logger.debug("Database name inference failed: %s", exc)

    svc_name = (service.name or '').lower().strip()
    if svc_name in _SERVICE_DB_MAP:
        return _SERVICE_DB_MAP[svc_name]

    return re.sub(r'[^a-z0-9_]', '_', svc_name)[:63]

def _ensure_database_exists(base_url: str, db_name: str):
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
