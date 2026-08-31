"""
Deployment utility functions.
"""
import logging
import os
import secrets

logger = logging.getLogger(__name__)


def get_default_env_value(key: str, scan_result: dict, service_name: str) -> tuple[str | None, bool]:
    key_upper = key.upper()

    if 'SECRET_KEY' in key_upper or key_upper == 'SECRET':
        return secrets.token_urlsafe(50), True

    if key_upper in ('JWT_SECRET', 'SESSION_SECRET', 'COOKIE_SECRET', 'CSRF_SECRET', 'SIGNING_KEY', 'HASH_SALT'):
        return secrets.token_urlsafe(48), True

    if key_upper in ('DATABASE_URL', 'DB_URL', 'DB_URI', 'SQLALCHEMY_DATABASE_URI', 'SQLALCHEMY_DATABASE_URL'):
        scan_result.get('stack', '')
        deps = scan_result.get('dependencies', [])
        dep_str = ' '.join(deps) if isinstance(deps, list) else str(deps)
        platform_db = os.environ.get('DATABASE_URL', 'postgresql://user:password@db:5432/dbname')
        if 'asyncpg' in dep_str or 'async' in dep_str.lower():
            if platform_db.startswith('postgresql://'):
                return platform_db.replace('postgresql://', 'postgresql+asyncpg://', 1), True
            return platform_db, True
        return platform_db, True

    if key_upper == 'REDIS_URL':
        return os.environ.get('REDIS_URL', 'redis://redis:6379/0'), True

    if key_upper in ('CELERY_BROKER_URL', 'BROKER_URL'):
        return os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/1'), True

    if key_upper in ('CELERY_RESULT_BACKEND', 'RESULT_BACKEND'):
        return os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/2'), True

    if key_upper in ('MONGODB_URI', 'MONGO_URI', 'MONGO_URL'):
        return os.environ.get('MONGODB_URI', 'mongodb://mongo:27017/dbname'), True

    if key_upper == 'PORT':
        return '8000', True

    if key_upper.endswith('_PORT'):
        return '8080', True

    if key_upper.endswith('_HOST') or key_upper.endswith('_HOSTNAME'):
        return 'localhost', True

    if key_upper in ('POSTGRES_USER', 'DB_USER'):
        return 'appuser', True

    if key_upper in ('POSTGRES_DB', 'DB_NAME'):
        return service_name.replace('-', '_')[:30], True

    if key_upper in ('POSTGRES_PASSWORD', 'DB_PASSWORD'):
        return secrets.token_urlsafe(48), True

    if key_upper in ('DEBUG', 'TESTING'):
        return 'false', True

    if key_upper in ('NODE_ENV', 'ENVIRONMENT'):
        return 'production', True

    # Wildcard host/CORS defaults are a security risk. Force the operator
    # to supply an explicit allow-list at the Caddy / Traefik layer.
    # The env value will end up as "" (empty) which every framework
    # interprets as "same-origin only" (Django ALLOWED_HOSTS, DRF,
    # django-cors-headers) — a safe default until the operator configures
    # a real allow-list.
    if key_upper in ('ALLOWED_HOSTS', 'DJANGO_ALLOWED_HOSTS', 'MARKETER_ALLOWED_HOSTS',
                     'CORS_ALLOWED_ORIGINS', 'CORS_ORIGINS', 'CORS_DEV_ORIGINS',
                     'ALLOWED_ORIGINS'):
        return '', True

    if key_upper in ('LOG_LEVEL',):
        return 'info', True

    if key_upper in ('WORKERS', 'WEB_CONCURRENCY'):
        return '4', True

    return None, False


def resolve_running_container(service, deployment=None):
    from apps.cloud.docker_client import get_docker_client

    if deployment is None:
        deployment = service.deployments.filter(status='ACTIVE').order_by('-created_at').first()
    if not deployment:
        return None

    client = get_docker_client()
    container_id = (deployment.container_id or "").strip()
    service_id = str(service.pk)

    if container_id:
        try:
            container = client.containers.get(container_id)
            if container.status == 'running':
                return container
        except Exception as exc:
            logger.debug("Container lookup by ID failed: %s", exc)

    try:
        containers = client.containers.list(
            filters={'label': f'smsly.service_id={service_id}', 'status': 'running'},
        )
        if containers:
            return containers[0]
    except Exception as exc:
        logger.debug("Container lookup by service_id label failed: %s", exc)

    try:
        containers = client.containers.list(
            filters={'name': service.name, 'status': 'running'},
        )
        if containers:
            return containers[0]
    except Exception as exc:
        logger.debug("Container lookup by name failed: %s", exc)

    return None


def is_deployment_local(deployment) -> bool:
    if bool(getattr(deployment, "target_is_local", False)):
        return True

    service = deployment.service
    server = getattr(deployment, "target_server", None) or getattr(service, "server", None)
    if not server:
        active_type = getattr(service, "active_target_type", None) or ""
        if active_type.lower() in ("remote", "lite_agent"):
            host_ip = getattr(service, "active_host_ip", None)
            if host_ip:
                from apps.deployments.models.core import ManagedServer
                srv = ManagedServer.objects.filter(host=host_ip).first()
                if srv:
                    server = srv
                else:
                    srv = ManagedServer.objects.filter(private_ip=host_ip).first()
                    if srv:
                        server = srv
                    else:
                        srv = ManagedServer.objects.filter(wg_address=host_ip).first()
                        if srv:
                            server = srv

    if not server:
        return True

    if bool(getattr(server, "is_primary", False)):
        return True

    from apps.deployments.models import PlatformConfig
    config = PlatformConfig.objects.first()
    server_ip = str(getattr(config, "server_ip", "") or "")
    return str(getattr(server, "host", "") or "") == server_ip
