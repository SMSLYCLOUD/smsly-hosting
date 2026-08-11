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
        })

    # Field mapping: API key → (PlatformConfig field, type)
    _PC_FIELDS = {
        # Auto-scaling
        'SCALE_MAX_REPLICAS': ('scale_max_replicas', int),
        'SCALE_CPU_HIGH': ('scale_cpu_high', int),
        'SCALE_COOLDOWN_MIN': ('scale_cooldown_min', int),
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
        # Feature flags / Security
        'GRID_ALLOW_CONTROL_PLANE_WORKLOADS': ('grid_allow_control_plane_workloads', bool),
        'ALLOW_INSECURE_INTER_NODE_TLS': ('allow_insecure_inter_node_tls', bool),
        'SMSLY_DISABLE_SIGNATURE_CHECK': ('smsly_disable_signature_check', bool),
        'SMSLY_DISABLE_TIER_GATES': ('smsly_disable_tier_gates', bool),
        'ENABLE_LEGACY_TUNNEL_API': ('enable_legacy_tunnel_api', bool),
        'SMSLY_STRICT_SSH_HOST_KEY_CHECK': ('smsly_strict_ssh_host_key_check', bool),
        'ENFORCE_DEVICE_TRUST': ('enforce_device_trust', bool),
    }

    _PC_SECRET_FIELDS = {'SMTP_PASSWORD', 'SMSLY_INTERNAL_API_KEY', 'REGISTRY_PASSWORD'}

    def _get_platform_config(self):
        pc, _ = PlatformConfig.objects.get_or_create(pk=1)
        result = {}
        for api_key, (field, _) in self._PC_FIELDS.items():
            result[api_key] = getattr(pc, field, None)
        return result

    def _get_autoscaling_config(self):
        pc, _ = PlatformConfig.objects.get_or_create(pk=1)
        return {
            'SCALE_MAX_REPLICAS': pc.scale_max_replicas,
            'SCALE_CPU_HIGH': pc.scale_cpu_high,
            'SCALE_COOLDOWN_MIN': pc.scale_cooldown_min,
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
        }

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
            'pgbouncer', 'pgbouncer-readonly',
            'redis', 'redis-primary', 'redis-replica',
            'redis-sentinel-1', 'redis-sentinel-2', 'redis-sentinel-3',
            'rabbitmq',
            'traefik', 'caddy', 'route-fallback', 'socket-proxy', 'frps',
            'grafana', 'loki', 'promtail', 'prometheus', 'alertmanager',
            'cadvisor', 'node-exporter',
            'crowdsec', 'smsly-falco', 'infisical',
            'registry', 'docker-mirror', 'verdaccio', 'buildkitd',
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
            for container_name, info in running_map.items():
                if _match_container_name(container_name, svc_name):
                    return info
            return None

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

        HTTP_PROBES = {
            'grafana': 'http://localhost:3001/api/health',
            'prometheus': 'http://localhost:9090/-/healthy',
            'loki': 'http://localhost:3100/ready',
            'rabbitmq': 'http://localhost:15672/api/health/checks/alarms',
            'pgcat': 'http://localhost:6432/pool',
            'alertmanager': 'http://localhost:9093/-/healthy',
        }

        def _http_probe(url: str) -> bool:
            try:
                import urllib.request
                req = urllib.request.Request(url, method='GET')
                resp = urllib.request.urlopen(req, timeout=2)
                return resp.status < 400
            except Exception:
                return False

        http_results = {}
        with ThreadPoolExecutor(max_workers=len(HTTP_PROBES)) as pool:
            futures = {pool.submit(_http_probe, url): svc for svc, url in HTTP_PROBES.items()}
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
                {"error": "Invalid maintenance action specified. Use clear, update, refresh, registry_gc, or build_cache."},
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
