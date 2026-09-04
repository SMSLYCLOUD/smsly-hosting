"""Standalone backup/restore operations."""

import logging
import os
import shlex
import time

import docker as _docker
from .s3 import delete_cloud_backup_object

logger = logging.getLogger(__name__)


def _dump_container_database(container_name, image_tag, temp_dir, docker_client=None):
    """Run pg_dump/mysqldump/redis SAVE inside a DB container for consistent backups.

    BUG FIX: the old code checked 'postgres' in (image_tag or '').lower()
    where image_tag was the BACKUP COMMIT TAG (backup/{name}:{uuid}) — it
    never contained 'postgres'/'mysql'/'redis', so the DB type detection
    always failed and the dump was silently skipped for every service.
    Now we inspect the CONTAINER's actual image instead.
    """
    if docker_client is not None:
        client = docker_client
    else:
        client = _docker.from_env()
    try:
        ctr = client.containers.get(container_name)
        # BUG FIX: detect the DB type from the CONTAINER's actual image,
        # not from the backup commit tag (which never contains db names)
        container_image = (ctr.attrs.get('Config', {}).get('Image', '') or '').lower()
        if not container_image:
            container_image = (ctr.image.tags[0] if ctr.image.tags else '').lower()
        image_lower = container_image
        dump_file = None

        if 'postgres' in image_lower:
            dump_file = os.path.join(temp_dir, 'db_dump.sql')
            c_env = {e.split('=', 1)[0]: e.split('=', 1)[1]
                     for e in (ctr.attrs.get('Config', {}).get('Env', []))
                     if '=' in e}
            pg_user = c_env.get('POSTGRES_USER', os.environ.get('POSTGRES_USER', 'smsly_admin'))
            pg_db = c_env.get('POSTGRES_DB', 'postgres')
            pg_password = c_env.get('POSTGRES_PASSWORD', os.environ.get('POSTGRES_PASSWORD', ''))
            result = ctr.exec_run(
                ['pg_dump', '-U', pg_user, '-d', pg_db,
                 '--lock-wait-timeout=5000',
                 '--clean', '--if-exists',
                 '--no-owner', '--no-acl'],
                environment={'PGPASSWORD': pg_password},
                timeout=600,
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                logger.info("pg_dump successful for %s (db: %s)", container_name, pg_db)
            else:
                logger.warning(
                    "pg_dump failed for %s (exit %s). Falling back to pg_dumpall.",
                    container_name, result.exit_code,
                )
                result = ctr.exec_run(
                    ['pg_dumpall', '-U', pg_user,
                     '--clean', '--if-exists',
                     '--no-role-passwords',
                     '--lock-wait-timeout=5000'],
                    environment={'PGPASSWORD': pg_password},
                    timeout=600,
                )
                if result.exit_code == 0:
                    with open(dump_file, 'wb') as f:
                        f.write(result.output)
                    logger.info("pg_dumpall fallback successful for %s", container_name)
                else:
                    raise RuntimeError(f"pg_dumpall failed for {container_name}: {result.output}")
        elif 'mysql' in image_lower or 'mariadb' in image_lower:
            dump_file = os.path.join(temp_dir, 'db_dump.sql')
            c_env = {e.split('=', 1)[0]: e.split('=', 1)[1]
                     for e in (ctr.attrs.get('Config', {}).get('Env', []))
                     if '=' in e}
            password = c_env.get('MYSQL_ROOT_PASSWORD', c_env.get('MYSQL_PASSWORD', ''))
            result = ctr.exec_run(
                ['mysqldump', '--all-databases', '-u', 'root'],
                environment={'MYSQL_PWD': password},
                timeout=600,
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                logger.info("mysqldump successful for %s", container_name)
            else:
                raise RuntimeError(f"mysqldump failed for {container_name}: {result.output}")
        elif 'redis' in image_lower:
            dump_file = os.path.join(temp_dir, 'redis_dump.rdb')
            ctr.exec_run(['redis-cli', 'SAVE'], timeout=120)
            time.sleep(2)
            bits, _ = ctr.get_archive('/data/dump.rdb')
            if bits:
                with open(dump_file, 'wb') as f:
                    for chunk in bits:
                        f.write(chunk)
                logger.info("Redis SAVE+backup successful for %s", container_name)
    except Exception as exc:
        logger.warning("DB dump for %s failed: %s", container_name, exc)
        raise RuntimeError(f"DB dump for {container_name} failed: {exc}") from exc


def _stop_service_for_restore(service, is_remote):
    """Gracefully stop a running container before restoring volumes. Waits for full stop."""
    container_name = service.name
    try:
        if is_remote:
            from apps.deployments.services.ssh_client import SSHClient
            server = service.server
            client = SSHClient(
                ip=server.host, password=server.ssh_password,
                user=server.ssh_user, port=server.ssh_port,
                key_content=server.ssh_key, wg_address=server.wg_address,
            )
            client.connect()
            try:
                safe_name = shlex.quote(container_name)
                client.exec_command(f"docker stop {safe_name} 2>/dev/null || true", raise_on_error=False)
                client.exec_command(
                    f"for i in $(seq 1 15); do "
                    f"  docker inspect -f '{{{{.State.Status}}}}' {safe_name} 2>/dev/null | grep -q exited && break; "
                    f"  sleep 1; "
                    f"done",
                    raise_on_error=False,
                )
            finally:
                client.close()
        else:
            import docker as _docker
            client = _docker.from_env()
            try:
                ctr = client.containers.get(container_name)
                ctr.stop(timeout=30)
                ctr.wait(condition='not-running', timeout=30)
            except Exception as stop_err:
                logger.warning(
                    "Failed to stop container for service %s (may still be running): %s",
                    getattr(service, 'name', 'unknown'), stop_err,
                )
        logger.info("Stopped service %s before restore", service.name)
    except Exception as exc:
        logger.warning("Could not stop service %s before restore: %s", service.name, exc)


def _emergency_restart_container(service):
    """Last-resort restart of a stopped container after restore."""
    container_name = service.name
    try:
        import docker as _docker
        client = _docker.from_env()
        ctr = client.containers.get(container_name)
        ctr.start()
        logger.info("Emergency restart: started container %s", container_name)
    except _docker.errors.NotFound:
        logger.warning("Emergency restart: container %s not found — service will stay stopped", container_name)
    except Exception as exc:
        logger.warning("Emergency restart failed for %s: %s", container_name, exc)


def _redeploy_restored_service_container(service):
    """Bring a restored service back up after archive extraction.

    The restore path previously called a non-existent
    `resolve_provider_for_service(...).deploy_service(...)` (ghost import
    introduced in d4aa419e) — every restore crashed with ImportError at
    the final redeploy step, after all the restore work was done.

    This helper does the pragmatic thing directly against Docker:
      1. If the container still exists (image-only/env-only restores),
         start it.
      2. If it does not exist (DB restores remove it), recreate it from
         the service's docker_image (set during image restore) with the
         service's env vars attached. Full re-provisioning (Traefik
         labels, networks) happens on the next deployment — a restore is
         an emergency recovery primitive, not a full deploy pipeline.

    Raises RuntimeError when neither can bring the container up.
    """
    container_name = service.name
    try:
        client = _docker.from_env()
    except Exception as exc:
        raise RuntimeError(f"Docker unavailable for restore redeploy: {exc}") from exc

    # 1. Existing container — just start it.
    try:
        ctr = client.containers.get(container_name)
        if ctr.status != 'running':
            ctr.start()
        logger.info("Restore redeploy: container %s is running", container_name)
        return
    except _docker.errors.NotFound:
        pass
    except Exception as exc:
        raise RuntimeError(f"Failed to inspect container {container_name}: {exc}") from exc

    # 2. Recreate from the restored image.
    image = str(getattr(service, 'docker_image', '') or '').strip()
    if not image:
        raise RuntimeError(
            f"Container {container_name} missing after restore and service "
            f"has no docker_image to recreate from"
        )

    from apps.deployments.models import EnvironmentVariable
    env_list = [
        f"{ev.key}={ev.value}"
        for ev in EnvironmentVariable.objects.filter(service=service)
        .exclude(key='').only('key', 'value')
    ]

    internal_port = int(getattr(service, 'internal_port', 0) or 3000)
    try:
        client.containers.run(
            image,
            name=container_name,
            environment=env_list,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            labels={
                "com.smsly.service": service.name,
                "smsly.service_id": str(service.id),
                "smsly.restored": "true",
            },
            ports={f"{internal_port}/tcp": None},
        )
        logger.info(
            "Restore redeploy: recreated container %s from image %s",
            container_name, image,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to recreate container {container_name} from {image}: {exc}"
        ) from exc


def _emergency_restart_remote_container(service, server):
    """Last-resort restart of a stopped container on a remote server after restore."""
    container_name = service.name
    try:
        from apps.deployments.services.ssh_client import SSHClient
        ssh = SSHClient(
            ip=server.host, password=server.ssh_password,
            user=server.ssh_user, port=server.ssh_port,
            key_content=server.ssh_key, wg_address=server.wg_address,
        )
        ssh.connect()
        safe_name = shlex.quote(container_name)
        ssh.exec_command(f"docker start {safe_name} 2>/dev/null || true", raise_on_error=False)
        ssh.close()
        logger.info("Emergency remote restart: started container %s on %s", container_name, server.host)
    except Exception as exc:
        logger.warning("Emergency remote restart failed for %s on %s: %s", container_name, getattr(server, 'host', '?'), exc)


def backup_addon(addon_id: str) -> str | None:
    """Back up a single addon (Postgres/MySQL/Redis/Mongo). Returns path to dump file or None."""

    from apps.deployments.models.addons import Addon
    client = _docker.from_env()
    try:
        addon = Addon.objects.get(id=addon_id, status='ACTIVE')
        ctr = client.containers.get(addon.container_name or addon.name)
        atype = (addon.addon_type or '').lower()
        backup_dir = os.path.join('/app', 'backups', 'addons', str(addon.service.id))
        os.makedirs(backup_dir, exist_ok=True)

        if 'postgres' in atype:
            dump_file = os.path.join(backup_dir, 'db_dump.sql')
            c_env = {e.split('=', 1)[0]: e.split('=', 1)[1]
                     for e in (ctr.attrs.get('Config', {}).get('Env', []))
                     if '=' in e}
            pg_user = c_env.get('POSTGRES_USER', 'postgres')
            pg_db = c_env.get('POSTGRES_DB', 'postgres')
            pg_password = c_env.get('POSTGRES_PASSWORD', '')
            result = ctr.exec_run(
                ['pg_dump', '-U', pg_user, '-d', pg_db, '--lock-wait-timeout=5000', '-c'],
                environment={'PGPASSWORD': pg_password},
                timeout=600,
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                return dump_file
            logger.warning(
                "pg_dump failed for addon %s (exit %s). Falling back to pg_dumpall.",
                addon_id, result.exit_code,
            )
            result = ctr.exec_run(
                ['pg_dumpall', '-U', pg_user, '-c', '--lock-wait-timeout=5000'],
                environment={'PGPASSWORD': pg_password},
                timeout=600,
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                return dump_file
            raise RuntimeError(f"Addon pg_dumpall failed with exit {result.exit_code}: {result.output}")
        elif 'mysql' in atype or 'mariadb' in atype:
            dump_file = os.path.join(backup_dir, 'db_dump.sql')
            c_env = {e.split('=', 1)[0]: e.split('=', 1)[1]
                     for e in (ctr.attrs.get('Config', {}).get('Env', []))
                     if '=' in e}
            password = c_env.get('MYSQL_ROOT_PASSWORD', c_env.get('MYSQL_PASSWORD', ''))
            result = ctr.exec_run(
                ['mysqldump', '--all-databases', '-u', 'root'],
                environment={'MYSQL_PWD': password},
                timeout=600,
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                return dump_file
            raise RuntimeError(f"Addon mysqldump failed with exit {result.exit_code}: {result.output}")
        elif 'redis' in atype:
            dump_file = os.path.join(backup_dir, 'redis_dump.rdb')
            ctr.exec_run(['redis-cli', 'SAVE'], timeout=120)
            time.sleep(1)
            bits, _ = ctr.get_archive('/data/dump.rdb')
            if bits:
                with open(dump_file, 'wb') as f:
                    for chunk in bits:
                        f.write(chunk)
                return dump_file
        elif 'mongo' in atype:
            dump_file = os.path.join(backup_dir, 'mongo_dump.archive')
            result = ctr.exec_run(['mongodump', '--archive=/tmp/mongo.archive', '--gzip'], timeout=600)
            if result.exit_code == 0:
                bits, _ = ctr.get_archive('/tmp/mongo.archive')
                if bits:
                    with open(dump_file, 'wb') as f:
                        for chunk in bits:
                            f.write(chunk)
                    return dump_file
            raise RuntimeError(f"mongodump failed for addon {addon_id}: exit {result.exit_code}")
    except Exception as exc:
        logger.warning("Addon backup failed for %s: %s", addon_id, exc)
    return None


def _remap_domain_on_restore(service, metadata):
    """If restoring to a different platform, remap the service's public_domain."""
    try:
        current_domain = os.environ.get('DOMAIN', '').strip()
        old_domain = (metadata or {}).get('platform_domain', '')
        if not current_domain or not old_domain or current_domain == old_domain:
            return

        import ipaddress
        try:
            ipaddress.ip_address(current_domain)
            return
        except ValueError:
            pass

        svc_domain = (service.public_domain or '').strip()
        if old_domain in svc_domain:
            new_domain = svc_domain.replace(old_domain, current_domain)
            service.public_domain = new_domain
            logger.info("Domain remapped on restore: %s → %s", svc_domain, new_domain)
    except Exception as exc:
        logger.warning("Failed to remap domain during restore: %s", exc)


def repair_double_encrypted_env_vars(service_id: str | None = None) -> dict:
    """
    Detect and repair EnvironmentVariables corrupted by pre-fix backup/restore
    double-encryption. Returns {fixed: N, skipped: N} counts.
    """
    from cryptography.fernet import Fernet, InvalidToken
    from django.conf import settings
    from django.db import connection

    key_str = getattr(settings, 'FIELD_ENCRYPTION_KEY', '')
    if not key_str:
        return {"error": "FIELD_ENCRYPTION_KEY not configured"}
    fernet = Fernet(key_str.encode() if isinstance(key_str, str) else key_str)

    from apps.deployments.models.core import EnvironmentVariable
    qs = EnvironmentVariable.objects.all()
    if service_id:
        qs = qs.filter(service_id=service_id)

    fixed = 0
    skipped = 0

    for ev in qs.iterator():
        raw_val = getattr(ev, 'value', '') or ''
        if not isinstance(raw_val, str) or not raw_val.startswith('gAAAAAB'):
            skipped += 1
            continue

        try:
            inner = fernet.decrypt(raw_val.encode())
            inner_str = inner.decode('utf-8', errors='replace')
            if not inner_str.startswith('gAAAAAB'):
                skipped += 1
                continue

            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE deployments_environmentvariable SET value = %s, updated_at = NOW() "
                    "WHERE id = %s",
                    [inner_str, str(ev.id)]
                )
            fixed += 1
        except (InvalidToken, Exception):
            skipped += 1
            continue

    return {"fixed": fixed, "skipped": skipped}


def purge_user_backups(user_id) -> dict:
    """
    GDPR right-to-erasure helper.

    Must be invoked BEFORE ``Service`` rows for the user are deleted, while
    the CASCADE FK on ``ServiceBackup.service`` still resolves.
    """
    from apps.deployments.models import Service
    from apps.cloud.models.backup import (
        ServerBackup,
        ServiceBackup,
    )

    from .cloud import _resolve_cloud_config

    counters = {
        'service_backups_deleted': 0,
        'service_backup_files_deleted': 0,
        'server_backups_deleted': 0,
        'server_backup_files_deleted': 0,
        'cloud_objects_deleted': 0,
        'errors': 0,
    }

    user_service_ids = list(
        Service.objects.filter(owner_id=user_id).values_list('id', flat=True)
    )

    service_backups = list(
        ServiceBackup.objects.select_related('service').filter(
            service__owner_id=user_id,
        )
    )

    for backup in service_backups:
        backup_service_id = getattr(backup, 'service_id', None)
        if backup.file_path and os.path.exists(backup.file_path):
            try:
                os.remove(backup.file_path)
                counters['service_backup_files_deleted'] += 1
                logger.info(
                    "GDPR: deleted backup file %s for service %s",
                    backup.file_path, backup_service_id,
                )
            except OSError as exc:
                counters['errors'] += 1
                logger.warning(
                    "GDPR: failed to delete backup file %s: %s",
                    backup.file_path, exc,
                )

        bucket, key, endpoint, region, access_key, secret_key = _resolve_cloud_config(backup)
        if bucket and key:
            if delete_cloud_backup_object(
                bucket, key, endpoint=endpoint, region=region,
                access_key=access_key, secret_key=secret_key,
            ):
                counters['cloud_objects_deleted'] += 1

    delete_result = ServiceBackup.objects.filter(
        service__owner_id=user_id,
    ).delete()
    counters['service_backups_deleted'] = delete_result[0]

    from django.db.models import Q
    query = Q()
    for sid in user_service_ids:
        query |= Q(services_included__contains=[str(sid)])
    try:
        server_backups = list(ServerBackup.objects.filter(query))
    except Exception:
        server_backups = [
            sb for sb in ServerBackup.objects.all()
            if sb.services_included and any(
                str(sid) in sb.services_included for sid in user_service_ids
            )
        ]
    for backup in server_backups:
        if getattr(backup, 'file_path', None) and os.path.exists(backup.file_path):
            try:
                os.remove(backup.file_path)
                counters['server_backup_files_deleted'] += 1
                logger.info(
                    "GDPR: deleted server backup file %s", backup.file_path,
                )
            except OSError as exc:
                counters['errors'] += 1
                logger.warning(
                    "GDPR: failed to delete server backup file %s: %s",
                    backup.file_path, exc,
                )
        if getattr(backup, 'cloud_uploaded', False):
            bucket, key, endpoint, region, access_key, secret_key = _resolve_cloud_config(backup)
            if bucket and key:
                if delete_cloud_backup_object(
                    bucket, key, endpoint=endpoint, region=region,
                    access_key=access_key, secret_key=secret_key,
                ):
                    counters['cloud_objects_deleted'] += 1
    if server_backups:
        ServerBackup.objects.filter(
            id__in=[sb.id for sb in server_backups]
        ).delete()
    counters['server_backups_deleted'] = len(server_backups)

    try:
        from apps.deployments.utils import log_event
        log_event(
            action='GDPR_BACKUP_PURGE',
            target=f'User: {user_id}',
            actor='system',
            metadata={
                'service_backups_deleted': counters.get('service_backups_deleted', 0),
                'server_backups_deleted': counters.get('server_backups_deleted', 0),
                'cloud_objects_deleted': counters.get('cloud_objects_deleted', 0),
            },
        )
    except Exception as exc:
        logger.warning("Failed to log GDPR backup purge event: %s", exc)

    return counters
