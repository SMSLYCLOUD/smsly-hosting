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
                environment={'PGPASSWORD': pg_password}
                ,
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
                    environment={'PGPASSWORD': pg_password}
                    ,
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
                environment={'MYSQL_PWD': password}
                ,
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                logger.info("mysqldump successful for %s", container_name)
            else:
                raise RuntimeError(f"mysqldump failed for {container_name}: {result.output}")
        elif 'redis' in image_lower:
            dump_file = os.path.join(temp_dir, 'redis_dump.rdb')
            ctr.exec_run(['redis-cli', 'SAVE'])
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
                ctr.wait(condition='not-running')
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


_ADDON_DUMPABLE_TYPES = frozenset({
    'POSTGRES', 'TIMESCALEDB', 'MYSQL', 'MARIADB', 'REDIS', 'MONGO', 'MONGODB',
})


def _addon_dump_filename(addon_name: str, kind: str) -> str:
    import re as _re_mod
    slug = _re_mod.sub(r'[^a-z0-9]+', '-', (addon_name or 'addon').lower()).strip('-')
    ext = {'REDIS': '.rdb', 'MONGO': '.archive', 'MONGODB': '.archive'}.get(kind, '.sql')
    return f'addon_{slug or "db"}_dump{ext}'


def _dump_service_addons(service, temp_dir, docker_client=None):
    """Dump every ACTIVE database addon of ``service`` into ``temp_dir``.

    The service-backup dump step (``_dump_container_database``) only
    handles databases INSIDE the app container. Addon-based services
    (app + addon postgres, e.g. marketer) silently produced env-only
    tarballs (2026-10-01: db_only backup with no postgres data).

    Returns a manifest list recorded in backup metadata['addon_dumps']
    (consumed by the restore path). Raises on any dump failure —
    shipping a tarball that silently omits an existing database is
    worse than a failed backup. Services without DB addons return []
    (stateless backups keep working unchanged).
    """
    from urllib.parse import urlparse as _urlparse
    if docker_client is not None:
        client = docker_client
    else:
        client = _docker.from_env()
    from apps.deployments.models.addons import Addon
    addons = list(Addon.objects.filter(
        service=service, status='ACTIVE',
        addon_type__in=sorted(_ADDON_DUMPABLE_TYPES),
    ).order_by('name'))
    manifest = []
    for addon in addons:
        kind = (addon.addon_type or '').upper()
        filename = _addon_dump_filename(addon.name, kind)
        dump_path = os.path.join(temp_dir, filename)
        parsed = _urlparse(addon.connection_url or '')
        url_user = parsed.username or ''
        url_db = (parsed.path or '/').lstrip('/') or ''
        url_password = parsed.password or ''
        if (addon.provision_mode or '') == 'shared' and kind in ('POSTGRES', 'TIMESCALEDB'):
            # Logical DB on the shared server: dump through it.
            from apps.addons.services.shared_postgres import SHARED_CONTAINER
            try:
                target = client.containers.get(SHARED_CONTAINER)
            except Exception as exc:
                raise RuntimeError(
                    f"Addon dump failed for {addon.name}: shared server unreachable: {exc}")
            if not (url_user and url_db and url_password):
                raise RuntimeError(
                    f"Addon dump failed for {addon.name}: connection_url incomplete")
            result = target.exec_run(
                ['pg_dump', '-h', '127.0.0.1', '-U', url_user, '-d', url_db,
                 '--lock-wait-timeout=5000', '--clean', '--if-exists',
                 '--no-owner', '--no-acl'],
                environment={'PGPASSWORD': url_password}
                ,
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Addon dump failed for {addon.name}: pg_dump exit "
                    f"{result.exit_code}: {(result.output or b'')[:200]}")
            with open(dump_path, 'wb') as f:
                f.write(result.output)
            logger.info("Addon dump successful for %s (shared %s)", addon.name, url_db)
            manifest.append({'addon': addon.name, 'addon_type': kind,
                             'provision_mode': 'shared', 'filename': filename,
                             'db': url_db})
            continue
        # Container addon: dump inside its own container. The canonical
        # dedicated-container name is smsly-addon-<type>-<uuid> (see the
        # provisioner); container_name is not a model field, so resolve
        # defensively instead of AttributeError-ing the whole backup.
        cname = (getattr(addon, 'container_name', None)
                 or f"smsly-addon-{(addon.addon_type or '').lower()}-{addon.id}")
        try:
            ctr = client.containers.get(cname)
        except Exception as exc:
            raise RuntimeError(
                f"Addon dump failed for {addon.name}: container {cname} unreachable: {exc}")
        c_env = {e.split('=', 1)[0]: e.split('=', 1)[1]
                 for e in (ctr.attrs.get('Config', {}).get('Env', []))
                 if '=' in e}
        if kind in ('POSTGRES', 'TIMESCALEDB'):
            pg_user = c_env.get('POSTGRES_USER') or url_user or 'postgres'
            pg_db = c_env.get('POSTGRES_DB') or url_db or 'postgres'
            pg_password = c_env.get('POSTGRES_PASSWORD') or url_password
            result = ctr.exec_run(
                ['pg_dump', '-U', pg_user, '-d', pg_db,
                 '--lock-wait-timeout=5000', '--clean', '--if-exists',
                 '--no-owner', '--no-acl'],
                environment={'PGPASSWORD': pg_password}
                ,
            )
            if result.exit_code != 0:
                result = ctr.exec_run(
                    ['pg_dumpall', '-U', pg_user,
                     '--clean', '--if-exists', '--no-role-passwords',
                     '--lock-wait-timeout=5000'],
                    environment={'PGPASSWORD': pg_password}
                    ,
                )
                if result.exit_code != 0:
                    raise RuntimeError(
                        f"Addon dump failed for {addon.name}: pg_dumpall exit "
                        f"{result.exit_code}: {(result.output or b'')[:200]}")
            with open(dump_path, 'wb') as f:
                f.write(result.output)
            logger.info("Addon dump successful for %s (db: %s)", addon.name, pg_db)
            manifest.append({'addon': addon.name, 'addon_type': kind,
                             'provision_mode': 'container', 'filename': filename,
                             'db': pg_db})
        elif kind in ('MYSQL', 'MARIADB'):
            password = c_env.get('MYSQL_ROOT_PASSWORD', c_env.get('MYSQL_PASSWORD', ''))
            result = ctr.exec_run(
                ['mysqldump', '--all-databases', '-u', 'root'],
                environment={'MYSQL_PWD': password}
                ,
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Addon dump failed for {addon.name}: mysqldump exit "
                    f"{result.exit_code}: {(result.output or b'')[:200]}")
            with open(dump_path, 'wb') as f:
                f.write(result.output)
            manifest.append({'addon': addon.name, 'addon_type': kind,
                             'provision_mode': 'container', 'filename': filename,
                             'db': 'all'})
        elif kind == 'REDIS':
            ctr.exec_run(['redis-cli', 'SAVE'])
            time.sleep(2)
            bits, _ = ctr.get_archive('/data/dump.rdb')
            if not bits:
                raise RuntimeError(f"Addon dump failed for {addon.name}: empty dump.rdb")
            with open(dump_path, 'wb') as f:
                for chunk in bits:
                    f.write(chunk)
            manifest.append({'addon': addon.name, 'addon_type': kind,
                             'provision_mode': 'container', 'filename': filename,
                             'db': 'rdb'})
        elif kind in ('MONGO', 'MONGODB'):
            result = ctr.exec_run(
                ['mongodump', '--archive=/tmp/mongo.archive', '--gzip'])
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Addon dump failed for {addon.name}: mongodump exit {result.exit_code}")
            bits, _ = ctr.get_archive('/tmp/mongo.archive')
            if not bits:
                raise RuntimeError(f"Addon dump failed for {addon.name}: empty archive")
            with open(dump_path, 'wb') as f:
                for chunk in bits:
                    f.write(chunk)
            manifest.append({'addon': addon.name, 'addon_type': kind,
                             'provision_mode': 'container', 'filename': filename,
                             'db': 'archive'})
    return manifest


def _backup_addon_volumes(service, temp_dir, docker_client=None):
    """Tar every named data volume of the service's ACTIVE addon containers.

    Addon data volumes (e.g. ``smsly-addon-postgres-<id>-data``) are not
    service-registered ``Volume`` rows, so neither service nor server
    backups captured them (2026-10-01: server backup had zero DB bytes).
    Returns (manifest, skipped): skipped names containers that are gone.
    Raises on tar failures (caller decides fail vs record-and-continue).
    """
    if docker_client is not None:
        client = docker_client
    else:
        client = _docker.from_env()
    from apps.deployments.models.addons import Addon
    import re as _re_mod
    manifest, skipped = [], []
    addons = list(Addon.objects.filter(
        service=service, status='ACTIVE').order_by('name'))
    for addon in addons:
        cname = (getattr(addon, 'container_name', None)
                 or f"smsly-addon-{(addon.addon_type or '').lower()}-{addon.id}")
        try:
            ctr = client.containers.get(cname)
        except Exception:
            skipped.append(addon.name)
            continue
        mounts = (ctr.attrs.get('Mounts', []) or [])
        for mount in mounts:
            if not isinstance(mount, dict) or mount.get('Type') != 'volume':
                continue
            vol_name = mount.get('Name', '')
            dest = mount.get('Destination', '')
            if not vol_name:
                continue
            safe = _re_mod.sub(r'[^A-Za-z0-9_.\-]+', '_', f'{cname}_{dest}')
            filename = f'addonvol_{safe}.tar.gz'
            vol_path = os.path.join(temp_dir, filename)
            helper = client.containers.run(
                'alpine:latest',
                command=['tar', '-czf', '-', '-C', '/volume_data', '.'],
                volumes={vol_name: {'bind': '/volume_data', 'mode': 'ro'}},
                detach=True, remove=False,
            )
            try:
                with open(vol_path, 'wb') as f:
                    for chunk in helper.logs(stream=True, stdout=True, stderr=False):
                        f.write(chunk)
                manifest.append({'service': getattr(service, 'name', ''),
                                 'addon': addon.name, 'container': cname,
                                 'volume': vol_name, 'destination': dest,
                                 'filename': filename})
                logger.info("Addon volume backup successful for %s (%s)",
                            addon.name, vol_name)
            finally:
                try:
                    helper.remove(force=True)
                except Exception:
                    pass
    return manifest, skipped


def _restore_shared_db(docker_client, entry, temp_dir, password):
    """Restore one shared-server database dump (manifest entry).

    The dump was taken with --create/--clean, so it is loaded through
    the maintenance database. Raises on any failure.
    """
    import re as _re_mod
    fname = (entry or {}).get('filename', '')
    dbname = (entry or {}).get('db', '')
    if not fname or not os.path.exists(os.path.join(temp_dir, fname)):
        raise RuntimeError(
            f"Shared dump {fname!r} listed in backup metadata is missing "
            f"from the archive.")
    if not _re_mod.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', dbname):
        raise RuntimeError(f"Shared dump has unsafe dbname {dbname!r}.")
    if not password:
        raise RuntimeError(
            'SHARED_POSTGRES_PASSWORD not configured — cannot restore '
            f'shared database {dbname}.')
    from apps.addons.services.shared_postgres import SHARED_CONTAINER
    from apps.cloud.services.backup_service.helpers import (
        _copy_file_to_container)
    target = docker_client.containers.get(SHARED_CONTAINER)
    _copy_file_to_container(
        docker_client, target.id,
        os.path.join(temp_dir, fname), '/tmp/restore_shared_dump.sql')
    res = target.exec_run(
        ['psql', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'postgres',
         '-f', '/tmp/restore_shared_dump.sql'],
        environment={'PGPASSWORD': password}
        ,
    )
    if res.exit_code != 0:
        raise RuntimeError(
            f"Shared restore failed for database {dbname} (exit "
            f"{res.exit_code}): {(res.output or b'')[:200]}")
    logger.info("Shared restore successful for database %s", dbname)


def _restore_addon_dump(docker_client, target_service, entry, temp_dir):
    """Restore one manifest addon-dump entry into the target service's addon.

    Raises on missing file/addon/credentials/restore failure — silently
    skipping a database restore is data loss.
    """
    import re as _re_mod
    from urllib.parse import urlparse as _urlparse
    fname = (entry or {}).get('filename', '')
    aname = (entry or {}).get('addon', '')
    if not fname or not os.path.exists(os.path.join(temp_dir, fname)):
        raise RuntimeError(
            f"Addon dump {fname!r} listed in backup metadata is missing "
            f"from the archive.")
    from apps.deployments.models.addons import Addon as _Addon
    addon = _Addon.objects.filter(
        service=target_service, name=aname, status='ACTIVE').first()
    if addon is None:
        raise RuntimeError(
            f"Restore needs addon {aname!r} on service "
            f"{target_service.name} — recreate it first, then retry.")
    if not fname.endswith('.sql'):
        raise RuntimeError(
            f"Addon dump {fname!r} needs manual restore (only .sql "
            f"restores are automated).")
    parsed = _urlparse(addon.connection_url or '')
    user = parsed.username or ''
    db = (parsed.path or '/').lstrip('/') or ''
    pw = parsed.password or ''
    if not (_re_mod.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', user)
            and _re_mod.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', db)
            and pw):
        raise RuntimeError(
            f"Addon {aname!r} has no usable live credentials for restore.")
    if (addon.provision_mode or '') == 'shared':
        from apps.addons.services.shared_postgres import SHARED_CONTAINER
        ctr = docker_client.containers.get(SHARED_CONTAINER)
        host_args = ['-h', '127.0.0.1']
    else:
        cname = (getattr(addon, 'container_name', None)
                 or f"smsly-addon-{(addon.addon_type or '').lower()}-{addon.id}")
        ctr = docker_client.containers.get(cname)
        host_args = []
    from apps.cloud.services.backup_service.helpers import (
        _copy_file_to_container)
    _copy_file_to_container(
        docker_client, ctr.id,
        os.path.join(temp_dir, fname), '/tmp/restore_addon_dump.sql')
    res = ctr.exec_run(
        ['psql', *host_args, '-U', user, '-d', db,
         '-f', '/tmp/restore_addon_dump.sql'],
        environment={'PGPASSWORD': pw}
        ,
    )
    if res.exit_code != 0:
        raise RuntimeError(
            f"Addon restore failed for {aname!r} (exit {res.exit_code}): "
            f"{(res.output or b'')[:200]}")
    logger.info("Addon restore successful for %s", aname)


def _dump_shared_server(temp_dir, docker_client=None, exclude_dbs=frozenset()):
    """Logical per-database dumps of the shared Postgres server.

    Covers shared-host databases with NO platform addon row (platform
    microservice DBs: policy, gateway, chain, …). Addon logical DBs are
    dumped per-addon already — pass their dbnames in ``exclude_dbs`` so
    they are not dumped twice.

    Credentials come from the ``SHARED_POSTGRES_PASSWORD`` env var on
    the backup worker (never the repo). Absent/unusable creds raise —
    the server-backup caller records the skip loudly instead of
    shipping a tarball that silently omits half the platform.
    """
    import os as _os
    password = (_os.environ.get('SHARED_POSTGRES_PASSWORD', '') or '').strip()
    if not password:
        raise RuntimeError(
            'SHARED_POSTGRES_PASSWORD not configured on the backup worker — '
            'set it in the hosting .env and recreate the backend.')
    if docker_client is not None:
        client = docker_client
    else:
        client = _docker.from_env()
    from apps.addons.services.shared_postgres import SHARED_CONTAINER
    try:
        target = client.containers.get(SHARED_CONTAINER)
    except Exception as exc:
        raise RuntimeError(f"shared server unreachable: {exc}")
    list_res = target.exec_run(
        ['psql', '-h', '127.0.0.1', '-U', 'postgres', '-tA',
         '-c', "SELECT datname FROM pg_database WHERE datistemplate = false;"],
        environment={'PGPASSWORD': password}
        ,
    )
    if list_res.exit_code != 0:
        raise RuntimeError(
            f"shared db listing failed (exit {list_res.exit_code}): "
            f"{(list_res.output or b'')[:200]}")
    import re as _re_mod
    manifest = []
    excluded = {str(d).strip() for d in (exclude_dbs or []) if str(d).strip()}
    for line in (list_res.output or b'').decode('utf-8', 'replace').splitlines():
        dbname = line.strip()
        if not dbname or dbname in excluded:
            continue
        if not _re_mod.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', dbname):
            logger.warning("shared dump: skipping odd dbname %r", dbname)
            continue
        filename = f'shared_db_{dbname}.sql'
        dump_path = os.path.join(temp_dir, filename)
        result = target.exec_run(
            ['pg_dump', '-h', '127.0.0.1', '-U', 'postgres', '-d', dbname,
             '--create', '--clean', '--if-exists',
             '--no-owner', '--no-acl', '--lock-wait-timeout=5000'],
            environment={'PGPASSWORD': password}
            ,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"shared dump failed for database {dbname} (exit "
                f"{result.exit_code}): {(result.output or b'')[:200]}")
        with open(dump_path, 'wb') as f:
            f.write(result.output)
        logger.info("shared dump successful for database %s", dbname)
        manifest.append({'db': dbname, 'filename': filename})
    return manifest


def backup_addon(addon_id: str) -> str | None:
    """Back up a single addon (Postgres/MySQL/Redis/Mongo). Returns path to dump file or None."""

    from apps.deployments.models.addons import Addon
    client = _docker.from_env()
    try:
        addon = Addon.objects.get(id=addon_id, status='ACTIVE')
        cname = (getattr(addon, 'container_name', None)
                 or f"smsly-addon-{(addon.addon_type or '').lower()}-{addon.id}")
        ctr = client.containers.get(cname)
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
                environment={'PGPASSWORD': pg_password}
                ,
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
                environment={'PGPASSWORD': pg_password}
                ,
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
                environment={'MYSQL_PWD': password}
                ,
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                return dump_file
            raise RuntimeError(f"Addon mysqldump failed with exit {result.exit_code}: {result.output}")
        elif 'redis' in atype:
            dump_file = os.path.join(backup_dir, 'redis_dump.rdb')
            ctr.exec_run(['redis-cli', 'SAVE'])
            time.sleep(1)
            bits, _ = ctr.get_archive('/data/dump.rdb')
            if bits:
                with open(dump_file, 'wb') as f:
                    for chunk in bits:
                        f.write(chunk)
                return dump_file
        elif 'mongo' in atype:
            dump_file = os.path.join(backup_dir, 'mongo_dump.archive')
            result = ctr.exec_run(['mongodump', '--archive=/tmp/mongo.archive', '--gzip'])
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
