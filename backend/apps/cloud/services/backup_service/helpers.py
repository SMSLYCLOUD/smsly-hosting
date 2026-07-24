"""Helper utilities for backup operations."""

import io
import logging
import os
import sys
import tarfile

from django.core.cache import cache

from .exceptions import _SENSITIVE_ENV_PATTERN

logger = logging.getLogger(__name__)


def _acquire_service_lock(service_id: str, operation: str) -> bool:
    """Try to acquire a Redis lock for a service operation."""
    lock_key = f"backup_lock:{service_id}"
    return cache.add(lock_key, operation, timeout=3600)


def _release_service_lock(service_id: str):
    """Release the Redis lock for a service operation."""
    lock_key = f"backup_lock:{service_id}"
    cache.delete(lock_key)


def _copy_file_to_container(docker_client, container_id: str, local_path: str,
                            dest_path: str) -> None:
    """Copy a local file into a Docker container via the docker-py API."""
    dest_dir = os.path.dirname(dest_path)
    dest_name = os.path.basename(dest_path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        ti = tarfile.TarInfo(name=dest_name)
        ti.size = os.path.getsize(local_path)
        with open(local_path, 'rb') as f:
            ti.type = tarfile.REGTYPE
            tar.addfile(ti, f)
    buf.seek(0)
    docker_client.api.put_archive(container_id, dest_dir, buf)


def _safe_tar_extractall(tar: tarfile.TarFile, dest: str) -> None:
    """Validate every member of ``tar`` and extract it into ``dest`` safely."""
    dest_real = os.path.realpath(dest)
    for member in tar.getmembers():
        name = member.name
        if name.startswith('/') or '..' in name:
            raise ValueError(f"Unsafe path in backup archive: {name}")
        if member.issym() or member.islnk():
            link_target = member.linkname
            if not link_target:
                raise ValueError(
                    f"Refusing link with empty target in backup archive: {name}"
                )
            if os.path.isabs(link_target):
                resolved_link = os.path.realpath(link_target)
            else:
                member_dir = os.path.dirname(os.path.join(dest_real, name))
                resolved_link = os.path.realpath(
                    os.path.join(member_dir, link_target)
                )
            if os.path.commonpath([dest_real, resolved_link]) != dest_real:
                raise ValueError(
                    f"Refusing link that escapes extract dir in backup "
                    f"archive: {name} -> {link_target}"
                )

    if sys.version_info >= (3, 12):
        tar.extractall(path=dest, filter='data')
    else:
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                continue
            tar.extract(member, path=dest)


def _redact_env_for_backup(env_path, dest_path):
    """Copy a .env file to *dest_path* with sensitive values replaced by REDACTED."""
    with open(env_path) as src, open(dest_path, 'w') as dst:
        for line in src:
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and '=' in stripped:
                key = stripped.split('=', 1)[0]
                if _SENSITIVE_ENV_PATTERN.search(key):
                    dst.write(f"{key}=REDACTED\n")
                    continue
            dst.write(line)
