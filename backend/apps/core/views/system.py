"""System configuration views."""
import logging
import os
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from celery.result import AsyncResult
from django.conf import settings
from django.core.cache import cache
from django.db import connection

from apps.deployments.models.core import PlatformConfig
from rest_framework import permissions, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.deployments.views._helpers import EmptySerializer, MAINTENANCE_ACTIONS

logger = logging.getLogger(__name__)


class SystemConfigView(GenericAPIView):
    """
    Expose safe server configuration to the frontend.
    GET /api/v1/system/config/
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def _maintenance_task_response(self, task_id: str):
        task_id = str(task_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", task_id):
            return Response(
                {"error": "Invalid maintenance task id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = AsyncResult(task_id)
        payload = {
            "task_id": task_id,
            "state": result.state,
            "status": "running",
            "message": "Maintenance task is still running.",
        }

        if result.state == "PENDING":
            payload["status"] = "queued"
            payload["message"] = "Maintenance task is queued or waiting for a worker."
        elif result.state == "STARTED":
            payload["status"] = "running"
        elif result.state == "SUCCESS":
            task_result = result.result or {}
            if isinstance(task_result, dict):
                payload.update({
                    "status": task_result.get("status", "success"),
                    "message": task_result.get("message", "Maintenance task completed."),
                    "result": task_result,
                })
            else:
                payload.update({
                    "status": "success",
                    "message": "Maintenance task completed.",
                    "result": task_result,
                })
        elif result.state == "FAILURE":
            payload.update({
                "status": "error",
                "message": str(result.result or "Maintenance task failed."),
            })
        elif isinstance(result.info, dict):
            payload.update({
                "status": result.info.get("status", payload["status"]),
                "message": result.info.get("message", payload["message"]),
                "meta": result.info,
            })

        return Response(payload)

    def get(self, request):
        task_id = (
            request.query_params.get("maintenance_task_id")
            or request.query_params.get("task_id")
        )
        if task_id:
            return self._maintenance_task_response(task_id)

        infra_health = self._get_infra_health()

        safe_data = {
            'VERSION': '3.0.0',
            'DOMAIN': getattr(settings, 'DOMAIN', 'localhost'),
            'safe_update_available': os.path.exists('/opt/smsly-hosting/scripts/safe-update.sh'),
            'MAPBOX_TOKEN': PlatformConfig.get_config_value('mapbox_token'),
            **self._get_storage_metrics(),
            **infra_health,
        }

        if not request.user.is_superuser:
            return Response(safe_data)

        return Response({
            **safe_data,
            # General
            'DEBUG': settings.DEBUG,
            'TIME_ZONE': settings.TIME_ZONE,
            'SITE_ID': settings.SITE_ID,

            # Security
            'USE_SSL': getattr(settings, 'SECURE_SSL_REDIRECT', False),
            'SECURE_SSL_REDIRECT': getattr(settings, 'SECURE_SSL_REDIRECT', False),
            'SECURE_HSTS_SECONDS': getattr(settings, 'SECURE_HSTS_SECONDS', 0),
            'SECURE_HSTS_INCLUDE_SUBDOMAINS': getattr(settings, 'SECURE_HSTS_INCLUDE_SUBDOMAINS', False),
            'SECURE_HSTS_PRELOAD': getattr(settings, 'SECURE_HSTS_PRELOAD', False),
            'SESSION_COOKIE_SECURE': getattr(settings, 'SESSION_COOKIE_SECURE', False),
            'CSRF_COOKIE_SECURE': getattr(settings, 'CSRF_COOKIE_SECURE', False),
            'SMSLY_DISABLE_SIGNATURE_CHECK': getattr(settings, 'SMSLY_DISABLE_SIGNATURE_CHECK', False),

            # Network
            'ALLOWED_HOSTS': settings.ALLOWED_HOSTS,
            'CORS_ALLOWED_ORIGINS': getattr(settings, 'CORS_ALLOWED_ORIGINS', []),
            'CSRF_TRUSTED_ORIGINS': getattr(settings, 'CSRF_TRUSTED_ORIGINS', []),

            # Auth
            'ACCOUNT_AUTH_METHOD': getattr(settings, 'ACCOUNT_AUTHENTICATION_METHOD', 'username'),
            'LOGIN_REDIRECT_URL': getattr(settings, 'LOGIN_REDIRECT_URL', '/'),

            # Infrastructure — Redis / Celery
            'REDIS_HOST': getattr(settings, 'REDIS_HOST', 'redis'),
            'REDIS_PORT': getattr(settings, 'REDIS_PORT', '6379'),
            'REDIS_PASSWORD_SET': bool(getattr(settings, 'REDIS_PASSWORD', '')),
            'CELERY_RESULT_BACKEND': getattr(settings, 'CELERY_RESULT_BACKEND', ''),

            # Container Registry
            'CONTAINER_REGISTRY_URL': getattr(settings, 'CONTAINER_REGISTRY_URL', ''),
            'REGISTRY_USER': getattr(settings, 'REGISTRY_USER', '') or 'Not set',
            'REGISTRY_PASSWORD_SET': bool(getattr(settings, 'REGISTRY_PASSWORD', '')),

            # Rate Limiting
            'THROTTLE_RATES': settings.REST_FRAMEWORK.get('DEFAULT_THROTTLE_RATES', {}),

            # Database (H-2 fix: expose only safe boolean flags, not internals)
            'DATABASE_CONFIGURED': bool(settings.DATABASES['default'].get('HOST')),
            'DATABASE_ENGINE_TYPE': 'postgres' if 'postgresql' in settings.DATABASES['default'].get('ENGINE', '') else 'other',

            # Webhook
            'GITHUB_WEBHOOK_SECRET_SET': bool(getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')),
            'GITLAB_WEBHOOK_SECRET_SET': bool(getattr(settings, 'GITLAB_WEBHOOK_SECRET', '')),
            'BITBUCKET_WEBHOOK_SECRET_SET': bool(getattr(settings, 'BITBUCKET_WEBHOOK_SECRET', '')),

            # Maintenance actions available to admins (labels only, no flags)
            'maintenance_actions': [
                {'action': key, 'label': spec['label']}
                for key, spec in MAINTENANCE_ACTIONS.items()
            ],

            # Auto-scaling config (DB-backed)
            **self._get_autoscaling_config(),

            # Platform config (DB-backed)
            **self._get_platform_config(),

            # Storage metrics
            **self._get_storage_metrics(),

            # Retention hygiene — env-driven with constants fallback
            # (matches apps/deployments/constants.py defaults; read-only
            # here because they are host env, not DB fields).
            'BUILD_CACHE_MAX_AGE_HOURS': max(1, int(os.environ.get('BUILD_CACHE_MAX_AGE_HOURS', 24))),
            'REGISTRY_TAG_RETENTION_DAYS': max(1, int(os.environ.get('REGISTRY_TAG_RETENTION_DAYS', 7))),
            'REGISTRY_TAG_DELETES_PER_CYCLE': 25,

            # Worker fleet live status: desired (DB) vs live (running
            # containers), pending restarts, queue coverage, beat cadences.
            **self._get_worker_fleet_status(),
        })

    # Field mapping: API key → (PlatformConfig field, type)
    _PC_FIELDS = {
        # Auto-scaling
        'SCALE_MAX_REPLICAS': ('scale_max_replicas', int),
        'SCALE_CPU_HIGH': ('scale_cpu_high', int),
        'SCALE_COOLDOWN_MIN': ('scale_cooldown_min', int),
        'NODE_SCORER_MIN_SCORE': ('node_scorer_min_score', int),
        'NODE_MIN_FREE_RAM_PCT': ('node_min_free_ram_pct', int),
        # Worker fleet concurrency (applied at worker (re)start)
        'CELERY_MAIN_MAX': ('celery_main_max', int),
        'CELERY_MAIN_MIN': ('celery_main_min', int),
        'CELERY_FAST_MAX': ('celery_fast_max', int),
        'CELERY_FAST_MIN': ('celery_fast_min', int),
        'CELERY_DEPLOY_MAX': ('celery_deploy_max', int),
        'CELERY_DEPLOY_MIN': ('celery_deploy_min', int),
        # Beat cadences (applied at beat restart)
        'MESH_HEALTH_INTERVAL': ('mesh_health_interval', int),
        'REPLICATION_HEALTH_INTERVAL': ('replication_health_interval', int),
        # Mesh DNS (zone served by CoreDNS; applied on next zone sync)
        'MESH_DNS_DOMAIN': ('mesh_dns_domain', str),
        # Email
        'SMTP_HOST': ('smtp_host', str),
        'SMTP_PORT': ('smtp_port', int),
        'SMTP_USERNAME': ('smtp_username', str),
        'SMTP_PASSWORD': ('smtp_password', str),
        'SMTP_USE_TLS': ('smtp_use_tls', bool),
        'SMTP_FROM_EMAIL': ('smtp_from_email', str),
        'SMTP_FROM_NAME': ('smtp_from_name', str),
        # Limits
        'MAX_UPLOAD_SIZE': ('max_upload_size', int),
        'SMSLY_MAX_FILE_READ_SIZE': ('smsly_max_file_read_size', int),
        'CADDY_DAILY_CERT_CAP': ('caddy_daily_cert_cap', int),
        # Rate Limiting
        'API_RATE_LIMIT': ('api_rate_limit', int),
        'API_RATE_LIMIT_FAIL_CLOSED': ('api_rate_limit_fail_closed', bool),
        # Logging
        'DJANGO_LOG_LEVEL': ('django_log_level', str),
        # Database HA
        'DB_HA_ENABLED': ('db_ha_enabled', bool),
        # Feature flags / Security
        'GRID_ALLOW_CONTROL_PLANE_WORKLOADS': ('grid_allow_control_plane_workloads', bool),
        'ALLOW_INSECURE_INTER_NODE_TLS': ('allow_insecure_inter_node_tls', bool),
        'SMSLY_DISABLE_SIGNATURE_CHECK': ('smsly_disable_signature_check', bool),
        'SMSLY_DISABLE_TIER_GATES': ('smsly_disable_tier_gates', bool),
        'ENABLE_LEGACY_TUNNEL_API': ('enable_legacy_tunnel_api', bool),
        'SMSLY_STRICT_SSH_HOST_KEY_CHECK': ('smsly_strict_ssh_host_key_check', bool),
        'ENFORCE_DEVICE_TRUST': ('enforce_device_trust', bool),
        # Blue-green rollback
        'ROLLBACK_GRACE_MINUTES': ('rollback_grace_minutes', int),
        'ROLLBACK_RETAIN_DEPLOYMENTS': ('rollback_retain_deployments', int),
        # Tenant pooling (pgcat-tenants for new shared Postgres addons)
        'TENANT_POOLING_ENABLED': ('tenant_pooling_enabled', bool),
        # Ecosystem
        'DEFAULT_ENV_SCAN_DEPTH': ('default_env_scan_depth', str),
        # Internal network (project-scoped bridges)
        'DEFAULT_INTERNAL_SUBNET': ('default_internal_subnet', str),
    }

    _PC_SECRET_FIELDS = {'SMTP_PASSWORD', 'SMSLY_INTERNAL_API_KEY', 'REGISTRY_PASSWORD'}

    def _get_platform_config(self):
        pc, _ = PlatformConfig.objects.get_or_create(pk=1)
        result = {}
        for api_key, (field, _) in self._PC_FIELDS.items():
            value = getattr(pc, field, None)
            if api_key in self._PC_SECRET_FIELDS:
                # Never echo secrets back (mirrors the domain-config
                # `*_set` pattern). Readers use KEY_SET; writers send a
                # fresh value, blank means "keep existing" (see patch).
                result[api_key] = ""
                result[api_key + "_SET"] = bool(value)
            else:
                result[api_key] = value
        return result

    def _get_autoscaling_config(self):
        pc, _ = PlatformConfig.objects.get_or_create(pk=1)
        return {
            'SCALE_MAX_REPLICAS': pc.scale_max_replicas,
            'SCALE_CPU_HIGH': pc.scale_cpu_high,
            'SCALE_COOLDOWN_MIN': pc.scale_cooldown_min,
            'NODE_SCORER_MIN_SCORE': pc.node_scorer_min_score,
            'NODE_MIN_FREE_RAM_PCT': pc.node_min_free_ram_pct,
        }

    def patch(self, request):
        if not request.user.is_superuser:
            return Response({'error': 'Admin only'}, status=403)
        data = request.data
        pc, _ = PlatformConfig.objects.get_or_create(pk=1)
        changed = []
        update_fields = []
        for api_key, (field, cast_type) in self._PC_FIELDS.items():
            if api_key in data:
                raw = data[api_key]
                if api_key == 'MESH_DNS_DOMAIN':
                    # A blank or malformed zone would break every mesh
                    # hostname at the next zone sync — reject instead of
                    # writing (blank means "keep existing", same as secrets).
                    domain = str(raw or '').strip().lower()
                    if not domain:
                        continue
                    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', domain):
                        return Response(
                            {'error': 'MESH_DNS_DOMAIN is not a valid DNS zone.'},
                            status=400,
                        )
                    setattr(pc, field, domain)
                    changed.append(api_key)
                    update_fields.append(field)
                    continue
                if api_key in self._PC_SECRET_FIELDS and not str(raw or "").strip():
                    # Blank means "keep existing" — a whole-config PUT from
                    # the UI must never wipe stored secrets with empty
                    # strings (same convention as domain-config's
                    # registry_password guard).
                    continue
                if cast_type == bool:
                    setattr(pc, field, bool(raw))
                elif cast_type == int:
                    setattr(pc, field, int(raw))
                else:
                    setattr(pc, field, str(raw) if raw is not None else '')
                changed.append(api_key)
                update_fields.append(field)
        if update_fields:
            pc.save(update_fields=update_fields)
            pc.clear_cache()
        return Response({
            'status': 'ok',
            'updated': changed,
            **self._get_platform_config(),
        })

    # ── Worker fleet live status (performance control plane) ──────────
    # Desired concurrency lives in PlatformConfig (CELERY_*_MAX/MIN plus
    # MESH/REPLICATION_HEALTH_INTERVAL — already exposed above via
    # _get_platform_config). What this adds:
    # - CELERY_LIVE: the --autoscale each worker container actually
    #   started with (docker inspect of the container Cmd, fail-open).
    # - CELERY_PENDING_RESTART: per-worker desired-vs-live mismatch.
    #   entrypoint.sh only rewrites --autoscale at (re)start, so a
    #   mismatch means "saved, restart workers to take effect".
    # - CELERY_QUEUE_COVERAGE: burst queues with min 0 must stay covered
    #   by the main worker's CELERY_QUEUES, else work stalls while burst
    #   workers sleep.
    # - BEAT_EFFECTIVE / BEAT_PENDING_RESTART: cadences resolve DB → env
    #   → literal at beat import; a DB-vs-running mismatch needs a beat
    #   restart to take effect.
    _FLEET_CONTAINERS = {
        'main': 'smsly-hosting-celery-1',
        'fast': 'smsly-hosting-celery-fast-1',
        'deploy': 'smsly-hosting-celery-deploy-1',
    }

    @staticmethod
    def _parse_autoscale_arg(args):
        """Pull '--autoscale=MAX,MIN' (or '--autoscale MAX,MIN') out of a
        container Cmd list. Returns the 'MAX,MIN' string or None."""
        if not args:
            return None
        for i, token in enumerate(args):
            text = str(token or '')
            if text.startswith('--autoscale='):
                return text.split('=', 1)[1].strip() or None
            if text == '--autoscale' and i + 1 < len(args):
                return str(args[i + 1] or '').strip() or None
        return None

    def _get_container_autoscale(self, name):
        """Live --autoscale of a running worker container, or None when
        the daemon is unreachable (fail-open: unknown, not an error)."""
        try:
            import docker

            container = docker.from_env(timeout=5).containers.get(name)
            attrs = container.attrs or {}
            cmd = attrs.get('Args') or (attrs.get('Config') or {}).get('Cmd') or []
            return self._parse_autoscale_arg(cmd)
        except Exception as exc:
            logger.debug("Fleet live probe failed for %s: %s", name, exc)
            return None

    @staticmethod
    def _coverage_warnings(main_queues, fast_min, deploy_min):
        """Warn for burst queues that no running worker would drain.

        Pure helper (unit-tested): a burst worker with min 0 sleeps when
        idle, so its queue must appear in the main worker's CELERY_QUEUES.
        """
        warnings = []
        queues = {q.strip() for q in (main_queues or '').split(',') if q.strip()}
        if int(fast_min or 0) == 0 and 'fast' not in queues:
            warnings.append(
                "fast queue uncovered: fast worker min is 0 and the main "
                "worker does not listen on 'fast' (CELERY_QUEUES). "
                "Heartbeat tasks would stall while the fast worker sleeps."
            )
        if int(deploy_min or 0) == 0 and 'deploy' not in queues:
            warnings.append(
                "deploy queue uncovered: deploy worker min is 0 and the "
                "main worker does not listen on 'deploy' (CELERY_QUEUES). "
                "Deploys would stall while the deploy worker sleeps."
            )
        return warnings

    def _get_worker_fleet_status(self):
        pc, _ = PlatformConfig.objects.get_or_create(pk=1)
        desired = {}
        live = {}
        pending = {}
        for role in ('main', 'fast', 'deploy'):
            max_v = getattr(pc, f'celery_{role}_max', None)
            min_v = getattr(pc, f'celery_{role}_min', None)
            try:
                desired[role] = f"{int(max_v)},{int(min_v)}"
            except (TypeError, ValueError):
                desired[role] = None
            live_v = self._get_container_autoscale(self._FLEET_CONTAINERS[role])
            live[role] = live_v
            pending[role] = bool(desired[role] and live_v and desired[role] != live_v)
        pending['any'] = any(pending.get(r) for r in ('main', 'fast', 'deploy'))
        main_queues = os.environ.get('CELERY_QUEUES', 'celery,fast,deploy')
        try:
            coverage = {
                'main_queues': [q for q in main_queues.split(',') if q.strip()],
                'warnings': self._coverage_warnings(
                    main_queues, pc.celery_fast_min, pc.celery_deploy_min),
            }
        except Exception as exc:
            logger.debug("Fleet coverage check failed: %s", exc)
            coverage = {'main_queues': [], 'warnings': []}
        try:
            mesh_desired = int(pc.mesh_health_interval or 120)
        except (TypeError, ValueError):
            mesh_desired = 120
        try:
            repl_desired = int(pc.replication_health_interval or 60)
        except (TypeError, ValueError):
            repl_desired = 60
        beat_effective = {'mesh_health_interval': None, 'replication_health_interval': None}
        try:
            from celery import current_app

            schedule = (current_app.conf.beat_schedule or {})
            mesh_entry = schedule.get('mesh-health-check-every-60s') or {}
            repl_entry = schedule.get('replication-health-every-30s') or {}
            if mesh_entry.get('schedule') is not None:
                beat_effective['mesh_health_interval'] = int(float(mesh_entry['schedule']))
            if repl_entry.get('schedule') is not None:
                beat_effective['replication_health_interval'] = int(float(repl_entry['schedule']))
        except Exception as exc:
            logger.debug("Beat effective probe failed: %s", exc)
        beat_desired = {
            'mesh_health_interval': mesh_desired,
            'replication_health_interval': repl_desired,
        }
        beat_pending = any(
            beat_effective[k] is not None and beat_effective[k] != beat_desired[k]
            for k in beat_desired
        )
        return {
            'CELERY_DESIRED': desired,
            'CELERY_LIVE': live,
            'CELERY_PENDING_RESTART': pending,
            'CELERY_QUEUE_COVERAGE': coverage,
            'BEAT_DESIRED': beat_desired,
            'BEAT_EFFECTIVE': beat_effective,
            'BEAT_PENDING_RESTART': beat_pending,
        }

    def _get_storage_metrics(self):
        """Fetch server root partition storage metrics using psutil or shutil."""
        import shutil
        try:
            total, used, free = shutil.disk_usage("/")
            return {
                'STORAGE_TOTAL_GB': round(total / (2**30), 2),
                'STORAGE_USED_GB': round(used / (2**30), 2),
                'STORAGE_FREE_GB': round(free / (2**30), 2),
                'STORAGE_USED_PERCENT': round((used / total) * 100, 1) if total > 0 else 0,
            }
        except Exception:
            return {
                'STORAGE_TOTAL_GB': 0,
                'STORAGE_USED_GB': 0,
                'STORAGE_FREE_GB': 0,
                'STORAGE_USED_PERCENT': 0,
            }

    def _get_redbeat_conn(self):
        """Redis connection on db 3 (redbeat lock lives there), or None."""
        try:
            from config.redis_sentinel import SENTINEL_ENABLED, get_master_connection
            if SENTINEL_ENABLED:
                return get_master_connection(
                    password=getattr(settings, 'REDIS_PASSWORD', None),
                    db=3,
                )
            import redis as redis_lib
            return redis_lib.Redis(
                host=getattr(settings, 'REDIS_HOST', 'redis'),
                port=int(getattr(settings, 'REDIS_PORT', 6379)),
                password=getattr(settings, 'REDIS_PASSWORD', '') or None,
                socket_timeout=2,
                db=3,
            )
        except Exception as exc:
            logger.debug("Redbeat redis connection failed: %s", exc)
            return None

    def _get_beat_status(self):
        """Redbeat scheduler lock state (Redis db 3, key ``redbeat::lock``).

        TTL semantics mirror ``ensure_beat_dispatching`` in
        ``scripts/verify_platform_integrity.sh``: a live holder extends the
        lock every tick, so a non-negative TTL means a beat holds it, -2
        means no lock (wedged or starting), -1 means persistent (unexpected).
        """
        try:
            lock_timeout = int(os.environ.get('REDBEAT_LOCK_TIMEOUT', 600))
        except (TypeError, ValueError):
            lock_timeout = 600
        result = {
            'scheduler': 'redbeat',
            'lock_timeout': max(300, min(lock_timeout, 3600)),
            'lock_ttl': None,
            'healthy': None,
        }
        try:
            conn = self._get_redbeat_conn()
            if conn is not None:
                ttl = conn.ttl('redbeat::lock')
                result['lock_ttl'] = int(ttl) if ttl is not None else None
                result['healthy'] = ttl is not None and int(ttl) >= 0
        except Exception as exc:
            logger.debug("Beat lock probe failed: %s", exc)
        return result

    def _get_infra_health(self):
        """Check live infrastructure health: host metrics + all PaaS services."""
        infra = {
            'cpu_percent': 0.0,
            'ram_total_mb': 0,
            'ram_used_mb': 0,
            'ram_percent': 0.0,
            'load_avg': [0.0, 0.0, 0.0],
            'disk_total_gb': 0.0,
            'disk_used_gb': 0.0,
            'disk_percent': 0.0,
        'uptime_seconds': 0,
        'services': {},
        'edge_warnings': [],
    }

        # ── Edge (Caddy) reload health ──────────────────────────────────
        # A failed Caddy reload leaves the edge serving a STALE config —
        # new domains get no certificate while everything looks green.
        # The failure is recorded persistently by the reload path (and by
        # the host-side watcher); surface it here so the dashboard can
        # warn loudly instead of failing silently (2026-09-10 incident).
        try:
            from apps.deployments.services.caddy_manager.apply import (
                read_caddy_reload_failure,
            )
            _reload_failure = read_caddy_reload_failure()
            if _reload_failure:
                import datetime as _dt
                _ts = _reload_failure.get("ts") or 0
                try:
                    _when = _dt.datetime.fromtimestamp(
                        float(_ts), tz=_dt.timezone.utc,
                    ).isoformat()
                except (TypeError, ValueError):
                    _when = "unknown time"
                infra['edge_warnings'].append({
                    'code': 'caddy_reload_failed',
                    'severity': 'critical',
                    'message': (
                        'Caddy failed to reload its configuration and is '
                        'serving a stale config. New domains will not get '
                        'TLS certificates.'
                    ),
                    'detail': str(_reload_failure.get("error") or "")[:300],
                    'since': _when,
                })
        except Exception as exc:
            logger.debug("Edge reload-health check failed: %s", exc)

        # ── Host metrics ──────────────────────────────────────────
        try:
            import psutil
            infra['cpu_percent'] = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            infra['ram_total_mb'] = round(mem.total / (1024 * 1024))
            infra['ram_used_mb'] = round(mem.used / (1024 * 1024))
            infra['ram_percent'] = mem.percent
            load = psutil.getloadavg()
            infra['load_avg'] = [round(x, 2) for x in load]
            disk = psutil.disk_usage('/')
            infra['disk_total_gb'] = round(disk.total / (2**30), 2)
            infra['disk_used_gb'] = round(disk.used / (2**30), 2)
            infra['disk_percent'] = round(disk.percent, 1)
            infra['uptime_seconds'] = int(time.time() - psutil.boot_time())
        except ImportError:
            try:
                with open('/proc/loadavg') as f:
                    parts = f.read().split()
                    infra['load_avg'] = [float(parts[i]) for i in range(3)]
            except (OSError, ValueError) as exc:
                logger.debug("Failed to read /proc/loadavg: %s", exc)

        # ── Docker containers ─────────────────────────────────────
        KNOWN_SERVICES = [
            'backend', 'frontend', 'celery', 'celery-beat', 'celery-fast', 'celery-deploy',
            'db', 'db-replica', 'postgres-primary', 'postgres-replica', 'pgcat',
            'pgcat-tenants', 'shared-postgres', 'shared-postgres-replica',
            'pgbouncer', 'pgbouncer-readonly',
            'redis', 'redis-primary', 'redis-replica',
            'redis-sentinel-1', 'redis-sentinel-2', 'redis-sentinel-3',
            'rabbitmq',
            'traefik', 'caddy', 'route-fallback', 'socket-proxy', 'frps',
            'grafana', 'loki', 'promtail', 'prometheus', 'alertmanager',
            'cadvisor', 'node-exporter', 'loki-log-bridge',
            'crowdsec', 'cloudflare-bouncer', 'smsly-falco', 'infisical',
            'spire-server', 'spire-agent',
            'spire-server-ecosystem', 'spire-agent-ecosystem',
            'appsec-agent', 'appsec-envoy', 'appsec-db',
            'appsec-smartsync', 'appsec-tuning-svc',
            'appsec-shared-storage',
            'registry', 'docker-mirror', 'verdaccio', 'buildkit',
            'mcp-server',
            'apt-cacher', 'docker-labels',
        ]

        def _match_container_name(container_name, svc_name):
            return bool(re.search(rf'(?:-|^){re.escape(svc_name)}(?:-\d+)?$', container_name))

        running_map = {}
        try:
            result = subprocess.run(
                ['docker', 'ps', '-a', '--format', '{{.Names}}\t{{.Status}}\t{{.State}}'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        name, status_str, state = parts[0].strip(), parts[1].strip(), parts[2].strip()
                        running_map[name] = {'status': status_str, 'running': state == 'running'}
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

        def _find_container(svc_name):
            # Prefer the shortest matching name: e.g. 'socket-proxy' must
            # match smsly-hosting-socket-proxy-1, not the longer
            # smsly-hosting-traefik-socket-proxy-1. Platform-owned names
            # (smsly-hosting-*) win over tenant containers first: without
            # this, 'backend' matches tenant smsly-backend instead of
            # smsly-hosting-backend-1 and the status page reports the
            # wrong container.
            platform_hits, other_hits = [], []
            for container_name, info in running_map.items():
                if _match_container_name(container_name, svc_name):
                    if svc_name == 'db' and container_name not in ('db',) and not container_name.startswith('smsly-hosting-db'):
                        # 'db' is a legacy key: anything ending -db (e.g.
                        # appsec-db) would match. Only the platform db
                        # counts; otherwise the SQL probe below decides.
                        continue
                    (platform_hits if container_name.startswith('smsly-hosting-')
                     else other_hits).append((container_name, info))
            best = None
            for hits in (platform_hits, other_hits):
                for container_name, info in hits:
                    if best is None or len(container_name) < len(best[0]):
                        best = (container_name, info)
                if best is not None:
                    break
            return best[1] if best else None

        # ── Service health probes ─────────────────────────────────
        db_ok = False
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                db_ok = True
        except Exception as exc:
            logger.debug("DB health probe failed: %s", exc)

        redis_ok = False
        try:
            from config.redis_sentinel import SENTINEL_ENABLED, get_master_connection
            if SENTINEL_ENABLED:
                conn = get_master_connection(
                    password=getattr(settings, 'REDIS_PASSWORD', None),
                    db=0,
                )
                if conn is not None:
                    conn.ping()
                    redis_ok = True
            else:
                import redis as redis_lib
                r = redis_lib.Redis(
                    host=getattr(settings, 'REDIS_HOST', 'redis'),
                    port=int(getattr(settings, 'REDIS_PORT', 6379)),
                    password=getattr(settings, 'REDIS_PASSWORD', '') or None,
                    socket_timeout=2,
                )
                r.ping()
                redis_ok = True
        except Exception as exc:
            logger.debug("Redis health probe failed: %s", exc)

        celery_ok = False
        try:
            from celery import app as celery_app
            inspect = celery_app.control.inspect(timeout=2)
            active = inspect.active()
            if active is not None:
                celery_ok = True
        except Exception as exc:
            logger.debug("Celery health probe failed: %s", exc)

        # NOTE: these probes run INSIDE the backend container, so they must
        # use in-network DNS names — never localhost (localhost here is the
        # backend container itself, not the host). RabbitMQ's management API
        # needs auth and pgcat speaks the Postgres wire protocol, so those
        # two are TCP-level checks instead of HTTP.
        HTTP_PROBES = {
            'grafana': 'http://smsly-grafana:3000/api/health',
            'prometheus': 'http://smsly-prometheus:9090/-/healthy',
            'loki': 'http://smsly-loki:3100/ready',
            'alertmanager': 'http://smsly-alertmanager:9093/-/healthy',
        }
        TCP_PROBES = {
            'rabbitmq': ('smsly-hosting-rabbitmq-1', 5672),
            'pgcat': ('smsly-hosting-pgcat-1', 6432),
            'pgcat-tenants': ('smsly-hosting-pgcat-tenants-1', 5432),
            'shared-postgres': ('smsly-shared-postgres', 5432),
        }

        def _buildkit_ok() -> bool:
            """Any buildx builder reporting running (seam for tests).

            BuildKit here is embedded/docker-container drivers, not a
            `buildkitd` container — container matching alone always misses.
            """
            try:
                result = subprocess.run(
                    ['docker', 'buildx', 'ls'],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    return False
                lines = (result.stdout or '').strip().split('\n')
                return any('running' in line for line in lines[1:])
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                return False

        def _http_probe(url: str) -> bool:
            try:
                import urllib.request
                req = urllib.request.Request(url, method='GET')
                resp = urllib.request.urlopen(req, timeout=2)
                return resp.status < 400
            except Exception:
                return False

        def _tcp_probe(host: str, port: int) -> bool:
            try:
                import socket
                with socket.create_connection((host, port), timeout=2):
                    return True
            except Exception:
                return False

        http_results = {}
        with ThreadPoolExecutor(max_workers=len(HTTP_PROBES) + len(TCP_PROBES)) as pool:
            futures = {pool.submit(_http_probe, url): svc for svc, url in HTTP_PROBES.items()}
            futures.update({
                pool.submit(_tcp_probe, host, port): svc
                for svc, (host, port) in TCP_PROBES.items()
            })
            for future in as_completed(futures):
                http_results[futures[future]] = future.result()

        for svc_name in KNOWN_SERVICES:
            container = _find_container(svc_name)
            if container:
                running = container['running']
            elif svc_name in ('db',):
                running = db_ok
            elif svc_name in ('redis', 'redis-primary'):
                running = redis_ok
            elif svc_name in ('celery', 'celery-beat', 'celery-fast', 'celery-deploy'):
                running = celery_ok
            elif svc_name in ('buildkit',):
                running = _buildkit_ok()
            elif svc_name in http_results:
                running = http_results[svc_name]
            else:
                running = False

            if svc_name in ('postgres-replica', 'redis-replica',
                            'redis-sentinel-1', 'redis-sentinel-2', 'redis-sentinel-3'):
                if container:
                    running = container['running']

            infra['services'][svc_name] = {
                'running': running,
                'status': container['status'] if container else ('healthy' if running else 'missing'),
            }

        # ── Beat scheduler lock ───────────────────────────────────
        # 2026-09-17: a recreated beat couldn't acquire redbeat::lock (dead
        # predecessor held it). Surface the lock TTL so the dashboard can
        # show scheduler health instead of failing silently.
        infra['beat'] = self._get_beat_status()

        # ── Host-level security ───────────────────────────────────
        host_security = {}

        try:
            result = subprocess.run(
                ['ufw', 'status'], capture_output=True, text=True, timeout=3,
            )
            host_security['ufw'] = {
                'installed': result.returncode == 0 or 'not found' not in (result.stderr or '').lower(),
                'active': 'active' in (result.stdout or '').lower(),
            }
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            host_security['ufw'] = {'installed': False, 'active': False}

        try:
            result = subprocess.run(
                ['fail2ban-client', 'ping'], capture_output=True, text=True, timeout=3,
            )
            host_security['fail2ban'] = {
                'installed': True,
                'active': 'pong' in (result.stdout or '').lower(),
            }
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            host_security['fail2ban'] = {'installed': False, 'active': False}

        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'auditd'],
                capture_output=True, text=True, timeout=3,
            )
            host_security['auditd'] = {
                'installed': result.returncode == 0 or 'could not be found' not in (result.stderr or '').lower(),
                'active': (result.stdout or '').strip() == 'active',
            }
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            host_security['auditd'] = {'installed': False, 'active': False}

        infra['host_security'] = host_security

        return infra

    def post(self, request):
        """Queue a maintenance task via the API."""
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return Response(
                {"error": "Admin privileges are required for maintenance actions."},
                status=status.HTTP_403_FORBIDDEN,
            )
        action = str(request.data.get('action') or '').strip().lower()
        action_spec = MAINTENANCE_ACTIONS.get(action)
        if not action_spec:
            return Response(
                {"error": "Invalid maintenance action specified. Use clear, update, refresh, registry_gc, build_cache, docker_recovery, restart_workers, or restart_beat."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.deployments.tasks.infra.tasks_maintenance import run_maintenance_task

        lock_key = f"smsly:maintenance:{action}:lock"
        task_id = str(uuid.uuid4())
        if not cache.add(lock_key, task_id, timeout=action_spec["lock_ttl"]):
            existing_task_id = cache.get(lock_key)
            return Response(
                {
                    "status": "running",
                    "action": action,
                    "task_id": existing_task_id,
                    "message": f"{action_spec['label']} is already running.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            task = run_maintenance_task.apply_async(
                kwargs={
                    "command_flag": action_spec["flag"],
                    "lock_key": lock_key,
                },
                task_id=task_id,
            )
        except Exception as exc:
            cache.delete(lock_key)
            logger.exception("Failed to queue maintenance action %s: %s", action, exc)
            return Response(
                {
                    "status": "error",
                    "action": action,
                    "message": "Failed to queue maintenance task. Check Celery/RabbitMQ availability.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = {
            "status": "queued",
            "action": action,
            "task_id": task.id or task_id,
            "message": action_spec["queued_message"],
        }

        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False) and task.ready():
            cache.delete(lock_key)
            task_result = task.result or {}
            if isinstance(task_result, dict):
                payload.update({
                    "status": task_result.get("status", "success"),
                    "message": task_result.get("message", payload["message"]),
                    "result": task_result,
                })
                status_code = (
                    status.HTTP_200_OK
                    if task_result.get("status") == "success"
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            else:
                payload.update({"status": "success", "result": task_result})
                status_code = status.HTTP_200_OK
            return Response(payload, status=status_code)

        return Response(payload, status=status.HTTP_202_ACCEPTED)


class BeatHealView(GenericAPIView):
    """POST /api/v1/system/beat-heal/ — release a stale redbeat lock + restart beat.

    Same gates as ``ensure_beat_dispatching`` in
    ``scripts/verify_platform_integrity.sh``: exactly one local beat
    container, started >15 min ago, silent for 15 min, and a lock TTL that
    proves staleness (a live holder extends every tick, so TTL > 300 means
    someone is alive). Refuses instead of guessing.
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    BEAT_CONTAINER = 'smsly-hosting-celery-beat-1'
    LOCK_KEY = 'redbeat::lock'
    LIVE_TTL_FLOOR = 300
    MIN_BEAT_AGE_S = 900

    def post(self, request):
        gate = self._check_gates()
        if gate.get('error'):
            return Response(gate, status=status.HTTP_409_CONFLICT)
        actions = []
        if gate['lock_ttl'] is not None and gate['lock_ttl'] >= 0:
            deleted = self._del_lock()
            if not deleted:
                return Response(
                    {'error': 'Stale lock confirmed but DEL failed — inspect redis db 3 manually.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            actions.append(f"released stale lock (ttl was {gate['lock_ttl']}s)")
        else:
            actions.append('no lock held — nothing to release')
        restart = self._docker('restart', self.BEAT_CONTAINER, timeout=90)
        if restart.get('error'):
            return Response(
                {'error': f"Lock released but beat restart failed: {restart['error']}",
                 'actions': actions},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        actions.append('restarted beat container')
        return Response({
            'status': 'ok',
            'actions': actions,
            'message': 'Beat lock healed. Watch /status for Dispatching within a few minutes.',
        })

    # -- gates (all must pass) ----------------------------------------
    def _check_gates(self):
        started_at = self._docker(
            'inspect', self.BEAT_CONTAINER,
            '--format', '{{.State.StartedAt}}', timeout=15)
        if started_at.get('error'):
            return {'error': 'Beat container not running locally — nothing to heal.'}
        age = self._parse_age_s(started_at.get('output', ''))
        if age is not None and age < self.MIN_BEAT_AGE_S:
            return {'error': f'Beat restarted {int(age)}s ago — schedules still warming up; retry later.'}
        peers = self._docker(
            'ps', '--format', '{{.Names}}', timeout=15).get('output', '')
        beat_peers = [n for n in peers.split() if 'celery-beat' in n]
        if len(beat_peers) > 1:
            return {'error': f'{len(beat_peers)} beat containers run locally — refusing auto-heal (possible live peer).'}
        if self._dispatch_count_15m() > 0:
            return {'error': 'Beat dispatched tasks in the last 15m — it is alive; no heal needed.'}
        ttl = self._lock_ttl()
        if ttl is None:
            return {'error': 'Lock state unreadable — check redis db 3 manually.'}
        if ttl == -1:
            return {'error': 'Lock is persistent (no TTL) — unexpected; inspect manually.'}
        if ttl > self.LIVE_TTL_FLOOR:
            return {'error': f'Lock looks live (ttl={ttl}s) — a holder may be extending it; inspect manually.'}
        return {'lock_ttl': ttl}

    # -- helpers --------------------------------------------------------
    def _docker(self, *args, timeout=30):
        try:
            result = subprocess.run(
                ['docker', *args],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                return {'error': (result.stderr or result.stdout or 'docker failed').strip()[:300]}
            return {'output': (result.stdout or '').strip()}
        except subprocess.TimeoutExpired:
            return {'error': 'docker command timed out'}
        except Exception as exc:
            return {'error': str(exc)[:300]}

    def _parse_age_s(self, started_at):
        try:
            from datetime import datetime, timezone
            ts = started_at.strip().strip("'")
            # docker: 2026-09-18T10:00:00.123456789Z (nanoseconds)
            ts = re.sub(r'(\.\d{6})\d+', r'\1', ts).replace('Z', '+00:00')
            started = datetime.fromisoformat(ts)
            return (datetime.now(timezone.utc) - started).total_seconds()
        except Exception:
            return None

    def _dispatch_count_15m(self):
        try:
            result = subprocess.run(
                ['docker', 'logs', self.BEAT_CONTAINER, '--since', '15m'],
                capture_output=True, text=True, timeout=25,
            )
            out = (result.stdout or '') + (result.stderr or '')
            return sum(1 for line in out.splitlines() if 'Sending due task' in line)
        except Exception:
            return -1

    def _lock_ttl(self):
        try:
            conn = SystemConfigView()._get_redbeat_conn()
            if conn is None:
                return None
            ttl = conn.ttl(self.LOCK_KEY)
            return int(ttl) if ttl is not None else None
        except Exception:
            return None

    def _del_lock(self):
        try:
            conn = SystemConfigView()._get_redbeat_conn()
            if conn is None:
                return False
            return bool(conn.delete(self.LOCK_KEY))
        except Exception:
            return False


class RouteFallbackView(GenericAPIView):
    """GET/PUT /api/v1/system/route-fallback/ — edit the edge 503 pages.

    The route-fallback Caddy container serves ``index.html`` ("waking up")
    and ``disabled.html`` ("route disabled") from ``/etc/rb-fallback`` via a
    read-only directory bind of ``infrastructure/route-fallback/``. Reads and
    writes go through ``docker cp`` against the live container, so edits take
    effect immediately (HTML is served per-request; no reload needed). The
    Caddyfile itself is intentionally NOT editable here.

    Every saved page must keep the request-ID contract
    (``http.request.uuid``) so the dashboard error boundaries and the
    auto-retry script keep working.
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    DIR = '/etc/rb-fallback'
    FILES = ('index.html', 'disabled.html')
    MAX_BYTES = 200 * 1024

    def get(self, request):
        container = self._container()
        if container is None:
            return Response(
                {'error': 'route-fallback container not found.'}, status=404)
        pages = {}
        for name in self.FILES:
            content = self._read_page(container, name)
            if content is None:
                return Response(
                    {'error': f'Could not read {name} from {container}.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            pages[name] = content
        return Response({'container': container, 'pages': pages})

    def put(self, request):
        pages = (request.data or {}).get('pages') or {}
        if not isinstance(pages, dict) or not pages:
            return Response(
                {'error': 'Body must be {"pages": {"index.html": "...", ...}}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        unknown = [k for k in pages if k not in self.FILES]
        if unknown:
            return Response(
                {'error': f'Unknown pages: {", ".join(unknown)}. Editable: {", ".join(self.FILES)}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        container = self._container()
        if container is None:
            return Response(
                {'error': 'route-fallback container not found.'}, status=404)
        saved = []
        for name, content in pages.items():
            error = self._validate(name, content)
            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)
        for name, content in pages.items():
            if not self._write_page(container, name, content):
                return Response(
                    {'error': f'Wrote {", ".join(saved)} but failed on {name}. Re-check the page.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            saved.append(name)
        return Response({'status': 'ok', 'saved': saved, 'container': container})

    # -- helpers --------------------------------------------------------
    def _container(self):
        try:
            result = subprocess.run(
                ['docker', 'ps', '--format', '{{.Names}}'],
                capture_output=True, text=True, timeout=15,
            )
            names = (result.stdout or '').split()
        except Exception:
            return None
        cands = [n for n in names if 'route-fallback' in n]
        if not cands:
            return None
        cands.sort(key=len)
        return cands[0]

    def _read_page(self, container, name):
        import io
        import tarfile
        try:
            result = subprocess.run(
                ['docker', 'cp', f'{container}:{self.DIR}/{name}', '-'],
                capture_output=True, timeout=20,
            )
            if result.returncode != 0 or not result.stdout:
                return None
            with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
                member = tar.next()
                if member is None:
                    return None
                f = tar.extractfile(member)
                if f is None:
                    return None
                return f.read().decode('utf-8')
        except Exception as exc:
            logger.debug("route-fallback read failed: %s", exc)
            return None

    def _validate(self, name, content):
        if not isinstance(content, str) or not content.strip():
            return f'{name} must be non-empty HTML.'
        if len(content.encode('utf-8')) > self.MAX_BYTES:
            return f'{name} exceeds {self.MAX_BYTES // 1024}KB.'
        if 'http.request.uuid' not in content:
            return (f'{name} must keep the request-ID contract '
                    '(http.request.uuid) — the page would break error correlation.')
        return ''

    def _write_page(self, container, name, content):
        import os
        import tempfile
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode='w', suffix='.html', delete=False, encoding='utf-8') as f:
                f.write(content)
                path = f.name
            result = subprocess.run(
                ['docker', 'cp', path, f'{container}:{self.DIR}/{name}'],
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode == 0
        except Exception as exc:
            logger.debug("route-fallback write failed: %s", exc)
            return False
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


class DatabaseHaToggleView(GenericAPIView):
    """
    POST /api/v1/system/db-ha-toggle/
    Body: { "enabled": true/false }

    Stops or starts the postgres-replica container and reconfigures pgcat.
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    COMPOSE_FILE = 'docker-compose.prod.yml'
    INSTALL_DIR = '/opt/smsly-hosting'

    def post(self, request):
        enabled = request.data.get('enabled')
        if enabled is None:
            return Response(
                {'error': 'enabled field is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        enabled = bool(enabled)
        config = PlatformConfig.load()

        if config.db_ha_enabled == enabled:
            return Response({
                'status': 'no_change',
                'db_ha_enabled': enabled,
                'message': 'PostgreSQL HA is already in the requested state.',
            })

        if enabled:
            result = self._start_replica()
        else:
            result = self._stop_replica()

        if result.get('error'):
            return Response(result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        config.db_ha_enabled = enabled
        config.save(update_fields=['db_ha_enabled'])

        return Response({
            'status': 'ok',
            'db_ha_enabled': enabled,
            'message': (
                'PostgreSQL HA enabled. Replica started and pgcat reconfigured.'
                if enabled
                else 'PostgreSQL HA disabled. Replica stopped. All queries route to primary.'
            ),
        })

    def _run_compose(self, args, timeout=150):
        import subprocess
        cmd = [
            'docker', 'compose', '-f', self.COMPOSE_FILE,
        ] + args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=timeout,
                cwd=self.INSTALL_DIR,
            )
            if result.returncode != 0:
                logger.error("compose command failed: %s\nstderr: %s", ' '.join(cmd), result.stderr)
                return {'error': result.stderr.strip() or 'Docker Compose command failed.'}
            return {'ok': True}
        except subprocess.TimeoutExpired:
            return {'error': 'Docker Compose command timed out.'}
        except Exception as exc:
            return {'error': str(exc)}

    def _start_replica(self):
        result = self._run_compose([
            '--profile', 'local-ha', 'up', '-d',
            '--wait', '--wait-timeout', '120',
            'postgres-replica',
        ], timeout=150)
        if result.get('error'):
            return result

        self._update_env('DB_REPLICA_HOSTS', 'postgres-replica:5432')

        pgcat_result = self._run_compose(['restart', 'pgcat'], timeout=30)
        if pgcat_result.get('error'):
            logger.warning("pgcat restart failed after enabling HA: %s", pgcat_result['error'])

        return {'ok': True}

    def _stop_replica(self):
        result = self._run_compose([
            'stop', '--timeout', '15', 'postgres-replica',
        ], timeout=30)

        self._update_env('DB_REPLICA_HOSTS', '')

        pgcat_result = self._run_compose(['restart', 'pgcat'], timeout=30)
        if pgcat_result.get('error'):
            logger.warning("pgcat restart failed after disabling HA: %s", pgcat_result['error'])

        return {'ok': True}

    def _update_env(self, key, value):
        import re
        env_path = os.path.join(self.INSTALL_DIR, '.env')
        try:
            with open(env_path, 'r') as f:
                content = f.read()
            pattern = rf'^{re.escape(key)}=.*$'
            if re.search(pattern, content, re.MULTILINE):
                content = re.sub(pattern, f'{key}={value}', content, flags=re.MULTILINE)
            else:
                content += f'\n{key}={value}\n'
            with open(env_path, 'w') as f:
                f.write(content)
        except Exception as exc:
            logger.error("Failed to update .env key %s: %s", key, exc)


class PlatformStorageOverviewView(GenericAPIView):
    """
    GET  /api/v1/system/storage-overview/
    Returns host root disk partition metrics, Docker storage breakdown (images, containers,
    volumes, build cache), and platform artifact stats.

    POST /api/v1/system/storage-overview/
    Executes a storage optimization or maintenance action:
      - 'prune_build_cache': Prunes BuildKit caches
      - 'prune_images': Prunes dangling Docker images
      - 'registry_gc': Runs private registry garbage collection
      - 'clear_containers': Cleans dead/orphaned containers and flushes cache dirs
      - 'clean_logs': Archives/cleans historical deployment build logs older than 14d
      - 'docker_recovery': Full containerd/builder recovery with daemon restart
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        import shutil
        from django.utils import timezone

        # 1. Host Root Partition Metrics
        try:
            total, used, free = shutil.disk_usage("/")
            used_pct = round((used / total) * 100, 1) if total > 0 else 0.0
            disk_info = {
                "total_gb": round(total / (1024 ** 3), 2),
                "used_gb": round(used / (1024 ** 3), 2),
                "free_gb": round(free / (1024 ** 3), 2),
                "used_percent": used_pct,
                "status": "critical" if used_pct >= 90 else ("warning" if used_pct >= 80 else "healthy"),
            }
        except Exception as exc:
            logger.debug("Failed to read disk usage: %s", exc)
            disk_info = {
                "total_gb": 0.0,
                "used_gb": 0.0,
                "free_gb": 0.0,
                "used_percent": 0.0,
                "status": "unknown",
            }

        # 2. Docker Engine Storage Breakdown
        docker_info = {
            "available": False,
            "images": {"count": 0, "size_gb": 0.0, "reclaimable_gb": 0.0},
            "containers": {"count": 0, "size_gb": 0.0},
            "volumes": {"count": 0, "size_gb": 0.0, "reclaimable_gb": 0.0},
            "build_cache": {"count": 0, "size_gb": 0.0, "reclaimable_gb": 0.0},
            "total_docker_gb": 0.0,
            "total_reclaimable_gb": 0.0,
        }

        try:
            import docker
            # NOTE: df takes ~5s idle and 10s+ while builds hammer the
            # daemon (seen live 2026-09-23: tile flipped Offline/N/A on a
            # busy daemon). Generous timeout plus a 10-min cache so the
            # tile only reads Offline when df never succeeds.
            # (fail-open throughout; timestamp shows data age).
            client = docker.from_env(timeout=25)
            df = cache.get("smsly:storage:df:v1")
            if df is None:
                df = client.df()
                try:
                    cache.set("smsly:storage:df:v1", df, 600)
                except Exception:
                    pass
            docker_info["available"] = True

            imgs = df.get("Images") or []
            imgs_size = sum(img.get("Size", 0) for img in imgs)
            imgs_reclaimable = sum(img.get("Size", 0) for img in imgs if img.get("Containers", 0) == 0)
            docker_info["images"] = {
                "count": len(imgs),
                "size_gb": round(imgs_size / (1024 ** 3), 2),
                "reclaimable_gb": round(imgs_reclaimable / (1024 ** 3), 2),
            }

            cntrs = df.get("Containers") or []
            cntrs_size = sum(c.get("SizeRw", 0) for c in cntrs)
            docker_info["containers"] = {
                "count": len(cntrs),
                "size_gb": round(cntrs_size / (1024 ** 3), 2),
            }

            vols = df.get("Volumes") or []
            vols_size = sum((v.get("UsageData") or {}).get("Size", 0) for v in vols)
            vols_reclaimable = sum(
                (v.get("UsageData") or {}).get("Size", 0)
                for v in vols
                if (v.get("UsageData") or {}).get("RefCount", 0) == 0
            )
            docker_info["volumes"] = {
                "count": len(vols),
                "size_gb": round(vols_size / (1024 ** 3), 2),
                "reclaimable_gb": round(vols_reclaimable / (1024 ** 3), 2),
            }

            bc = df.get("BuildCache") or []
            bc_size = sum(b.get("Size", 0) for b in bc)
            bc_reclaimable = sum(b.get("Size", 0) for b in bc if not b.get("InUse", False))
            docker_info["build_cache"] = {
                "count": len(bc),
                "size_gb": round(bc_size / (1024 ** 3), 2),
                "reclaimable_gb": round(bc_reclaimable / (1024 ** 3), 2),
            }

            total_dock = imgs_size + cntrs_size + vols_size + bc_size
            total_reclaim = imgs_reclaimable + vols_reclaimable + bc_reclaimable
            docker_info["total_docker_gb"] = round(total_dock / (1024 ** 3), 2)
            docker_info["total_reclaimable_gb"] = round(total_reclaim / (1024 ** 3), 2)
        except Exception as exc:
            logger.debug("Docker df query failed or unavailable: %s", exc)

        # 3. Artifacts / Logs Breakdown
        artifacts_info = {
            "deployments_count": 0,
            "build_logs_mb": 0.0,
            "active_services": 0,
            "stale_builds_count": 0,
        }
        try:
            from apps.deployments.models import Deployment, Service
            artifacts_info["deployments_count"] = Deployment.objects.count()
            artifacts_info["active_services"] = Service.objects.count()
            has_logs_count = Deployment.objects.exclude(build_logs="").count()
            artifacts_info["build_logs_mb"] = round((has_logs_count * 50) / 1024, 2)
            artifacts_info["stale_builds_count"] = Deployment.objects.filter(
                status__in=[Deployment.Status.FAILED, Deployment.Status.CANCELLED, Deployment.Status.SUPERSEDED]
            ).count()
        except Exception as exc:
            logger.debug("Artifacts telemetry failed: %s", exc)

        return Response({
            "disk": disk_info,
            "docker": docker_info,
            "artifacts": artifacts_info,
            "timestamp": timezone.now().isoformat(),
        })

    def post(self, request):
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return Response({"error": "Admin privileges required"}, status=status.HTTP_403_FORBIDDEN)

        action = str(request.data.get("action") or "").strip().lower()
        from apps.deployments.tasks.infra.tasks_maintenance import run_maintenance_task

        flag_map = {
            "prune_build_cache": "--clear-build-cache",
            "build_cache": "--clear-build-cache",
            "prune_images": "--prune-images",
            "registry_gc": "--gc",
            "clear_containers": "--clear",
            "clear": "--clear",
            "clean_logs": "--clean-logs",
            "docker_recovery": "--docker-recovery",
        }

        command_flag = flag_map.get(action)
        if not command_flag:
            return Response({
                "error": f"Invalid action '{action}'. Valid actions: prune_build_cache, prune_images, registry_gc, clear_containers, clean_logs, docker_recovery"
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            task = run_maintenance_task.apply_async(kwargs={"command_flag": command_flag})
            return Response({
                "status": "queued",
                "task_id": task.id,
                "action": action,
                "message": f"Storage action '{action}' queued successfully.",
            }, status=status.HTTP_202_ACCEPTED)
        except Exception as exc:
            logger.exception("Failed to dispatch storage action %s: %s", action, exc)
            return Response({
                "error": "Failed to queue maintenance task. Check broker availability.",
                "details": str(exc),
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

