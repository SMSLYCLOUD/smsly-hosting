import base64
import binascii
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import struct
import sys
import tarfile
import tempfile
import time
import traceback
import uuid

import docker
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, hmac, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.utils.text import slugify

from apps.deployments.models import EnvironmentVariable, Service
from apps.deployments.models_backup import ServerBackup, ServiceBackup
from apps.deployments.models_storage import Volume

logger = logging.getLogger(__name__)


def _copy_file_to_container(docker_client, container_id: str, local_path: str,
                            dest_path: str) -> None:
    """Copy a local file into a Docker container via the docker-py API.

    Uses ``put_archive`` with an in-memory tar stream so no
    subprocess or docker CLI dependency is needed.  ``dest_path``
    must be an absolute path inside the container including the
    filename (e.g. ``/tmp/db_dump.sql``).
    """
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
    """Validate every member of ``tar`` and extract it into ``dest`` safely.

    Refuses to extract:
      * members whose path starts with ``/`` or contains ``..`` (path traversal);
      * symbolic or hard links whose target resolves outside ``dest``;
      * any symbolic or hard link if the link target cannot be resolved safely.

    On Python 3.12+ we additionally rely on ``filter='data'`` so that the
    interpreter itself rejects symlinks/hardlinks/devices at extract time.
    """
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
            # Compute the resolved target path. For absolute links this is
            # the link path itself; for relative links it is interpreted
            # relative to the member's directory inside the archive.
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
        # The explicit per-member checks above already reject symlinks/hard
        # links whose target leaves ``dest``. We additionally skip the
        # remaining link-type members (which we have validated) to match
        # ``filter='data'`` semantics on older interpreters.
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                continue
            tar.extract(member, path=dest)


class BackupEncryptionRequired(Exception):
    """Raised when BACKUP_REQUIRE_ENCRYPTION is set but BACKUP_ENCRYPTION_KEY is missing."""
    pass


class UnknownBackupKeyIdError(Exception):
    """Raised when a V2 backup's key_id is not registered on this master.

    The caller should respond with a 400 + key_id + expected_fingerprint
    so the operator can either re-run with the correct key or call
    ``POST /backups/import-key/`` to register the source's key on this
    master. After import, the restore can be retried and the key will
    resolve automatically.
    """
    def __init__(self, key_id: str, fingerprint: str, message: str = ''):
        self.key_id = key_id
        self.fingerprint = fingerprint
        super().__init__(message or f"Unknown backup key_id={key_id}")


class BackupKeyCollisionError(Exception):
    """Raised when importing a key whose key_id collides with an existing
    row that has different key material (likely a 1-in-2^32 random collision
    or an attempted key-swap attack)."""
    pass


_CHUNKED_BACKUP_MAGIC = b"SMSLY-BACKUP-AESGCM-V1\n"
_CHUNKED_BACKUP_V2_MAGIC = b"SMSLY-BACKUP-AESGCM-V2\n"
_CHUNKED_BACKUP_V3_MAGIC = b"SMSLY-BACKUP-AESGCM-V3\n"
_CHUNKED_BACKUP_NONCE_PREFIX_BYTES = 8
_CHUNKED_BACKUP_KEY_ID_BYTES = 4
_CHUNKED_BACKUP_FINGERPRINT_BYTES = 4
_DEFAULT_CRYPTO_CHUNK_SIZE = 4 * 1024 * 1024
_FERNET_HEADER_SIZE = 1 + 8 + 16
_FERNET_HMAC_SIZE = 32

# Maximum backup archive size in bytes (default 50 GB).
# Controlled by BACKUP_MAX_SIZE_BYTES env var.
_DEFAULT_MAX_BACKUP_SIZE = 50 * 1024 * 1024 * 1024

# Pattern matching environment variable names that hold secrets.
_SENSITIVE_ENV_PATTERN = re.compile(
    r'(PASSWORD|SECRET|KEY|TOKEN|CREDENTIAL|API_KEY|PRIVATE)',
    re.IGNORECASE,
)


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


class BackupService:
    @staticmethod
    def _get_encryption_key():
        key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()
        if not key:
            try:
                from django.conf import settings
                key = getattr(settings, "BACKUP_ENCRYPTION_KEY", "").strip()
            except ImportError:
                pass
        if not key:
            try:
                from apps.deployments.models_backup import BackupEncryptionKey
                active = BackupEncryptionKey.objects.filter(is_active=True).first()
                if active and active.key_material_encrypted:
                    key = active.key_material_encrypted.strip()
            except Exception:
                pass
        return key
    def __init__(self):
        try:
            from apps.cloud.docker_client import get_docker_client
            self.docker_client = get_docker_client(timeout=120)
        except Exception as e:
            logger.warning("Docker client init failed (backups requiring Docker will fail): %s", e)
            self.docker_client = None

    @staticmethod
    def _get_backups_dir(subdir: str) -> str:
        """Get or create a writable backups directory.

        Always uses the persistent Docker volume at /app/backups/{subdir}.
        The previous implementation fell back to /tmp/backups/ when the
        primary path was not writable, but /tmp is ephemeral — a container
        restart would delete all backups while the DB records still pointed
        to them, causing 404 on download. Now we fail loudly instead of
        silently writing to a non-persistent location.
        """
        primary = os.path.join('/app', 'backups', subdir)
        os.makedirs(primary, exist_ok=True)
        # Test write access by creating a temp file
        test_file = os.path.join(primary, '.write_test')
        try:
            with open(test_file, 'w') as f:
                f.write('ok')
            os.remove(test_file)
            return primary
        except (PermissionError, OSError) as e:
            raise RuntimeError(
                f"Cannot write to backup directory {primary}: {e}. "
                "Check that the backups_data volume is mounted and writable."
            ) from e

    def _prepare_archive_for_restore(self, backup) -> tuple[str, str | None]:
        """Return readable tar.gz path, verifying checksum if backup object has metadata."""
        # Accept both a backup object or a raw filepath string
        if isinstance(backup, str):
            path = backup
            expected_hash = None
            expected_size = 0
        else:
            path = backup.file_path
            expected_hash = (getattr(backup, 'metadata', None) or {}).get('checksum_sha256', '')
            expected_size = getattr(backup, 'size_bytes', 0) or 0
        if not path or not os.path.exists(path):
            # File missing locally — try to download from cloud storage
            if not isinstance(backup, str) and getattr(backup, 'cloud_uploaded', False):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if _download_backup_from_cloud(backup, path):
                    logger.info("Downloaded backup %s from cloud to %s", backup.id, path)
                else:
                    raise FileNotFoundError(
                        f"Backup file not found locally and cloud download failed. "
                        f"backup_id={backup.id}"
                    )
            else:
                raise FileNotFoundError("Backup archive file not found.")
        if expected_size and os.path.getsize(path) != expected_size:
            raise ValueError(f"Size mismatch: expected {expected_size}, got {os.path.getsize(path)}")
        if expected_hash:
            sha = hashlib.sha256()
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha.update(chunk)
            if sha.hexdigest() != expected_hash:
                raise ValueError("Checksum mismatch — backup may be corrupted")
        if not path.endswith(".enc"):
            return path, None

        key = BackupService._get_encryption_key()
        if not key:
            raise ValueError("Encrypted backup detected but BACKUP_ENCRYPTION_KEY is not set.")
        decrypted_path = BackupService.decrypt_backup(path, key)
        return decrypted_path, decrypted_path

    def backup_service(self, service_id, backup_id=None, backup_type='MANUAL', db_only=False) -> ServiceBackup:
        service = Service.objects.get(id=service_id)

        if backup_id:
            try:
                backup = ServiceBackup.objects.get(id=backup_id)
                backup.status = 'IN_PROGRESS'
                backup.error_message = ''
                backup.save(update_fields=['status', 'error_message'])
            except ServiceBackup.DoesNotExist:
                backup = ServiceBackup.objects.create(
                    service=service,
                    status='IN_PROGRESS',
                    backup_type=backup_type,
                    db_only=db_only
                )
        else:
            backup = ServiceBackup.objects.create(
                service=service,
                status='IN_PROGRESS',
                backup_type=backup_type,
                db_only=db_only
            )

        try:
            from apps.deployments.utils_target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            if target["target_type"] in ("remote", "lite_agent") and target["server_obj"]:
                include_secret_values = str(backup_type or '').upper() in {
                    'TRANSFER',
                    'SERVICE_TRANSFER',
                    'SERVER_TRANSFER',
                    'PRE_TRANSFER',
                }
                return self._backup_remote_service(service, backup, target["server_obj"], include_secret_values)
        except Exception as e:
            if backup.status == 'FAILED':
                raise
            logger.warning("Target resolution failed for backup: %s", e)

        if not self.docker_client:
            backup.status = 'FAILED'
            backup.error_message = (
                "Docker is not available. Backups require a running Docker daemon."
            )
            backup.save(update_fields=['status', 'error_message'])
            raise RuntimeError(
                "Docker is not available. Backups require a running Docker daemon. "
                "Please ensure Docker is installed and accessible."
            )

        temp_dir = None
        try:
            # Snapshot env vars. Operator/downloadable backups mask secrets, but
            # transfer backups must preserve them so restored services can boot.
            include_secret_values = str(backup_type or '').upper() in {
                'TRANSFER',
                'SERVICE_TRANSFER',
                'SERVER_TRANSFER',
                'PRE_TRANSFER',
            }
            env_vars_raw = [
                {"key": ev.key, "value": ev.value, "is_secret": ev.is_secret}
                for ev in EnvironmentVariable.objects.filter(service=service).only('key', 'value', 'is_secret')
            ]
            env_vars = []
            for ev in env_vars_raw:
                entry = dict(ev)
                if entry.get('is_secret') and not include_secret_values:
                    entry['value'] = '********'
                env_vars.append(entry)

            # Save metadata
            metadata = {
                'service_name': service.name,
                'service_id': str(service.id),
                'platform_domain': os.environ.get('DOMAIN', ''),
                'deploy_type': service.deploy_type,
                'buildpack': service.buildpack,
                'env_vars': env_vars,
                'secrets_included': include_secret_values,
                'git_url': service.repository_url,
                'branch': service.branch,
                'public_domain': service.public_domain,
                'created_at': str(timezone.now()),
                'volumes': []
            }

            # Prepare directories
            backups_dir = self._get_backups_dir('services')

            # Temporary build directory for assembling the tarball
            temp_dir = os.path.join(backups_dir, f"tmp_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)

            # 1. Backup Docker Image (Skip if db_only)
            image_filename = "image.tar"
            image_path = os.path.join(temp_dir, image_filename)

            # Try to find running container to commit (most accurate state)
            image_tag = None
            try:
                container = self.docker_client.containers.get(service.name)
                # Commit container to a temp image
                repo = f"backup/{slugify(service.name)}"
                tag = f"{uuid.uuid4().hex[:8]}"
                image_tag = f"{repo}:{tag}"
                if not backup.db_only:
                    container.commit(repository=repo, tag=tag)
                    logger.info(f"Committed container {service.name} to {image_tag}")
            except docker.errors.NotFound:
                # If not running, fall back to the service's configured image
                if service.docker_image:
                    image_tag = service.docker_image
                    # Ensure we have it locally, otherwise pull?
                    try:
                        self.docker_client.images.get(image_tag)
                    except docker.errors.ImageNotFound:
                        # Attempt pull if it's a remote image
                        try:
                            self.docker_client.images.pull(image_tag)
                        except Exception as e:
                            logger.warning(f"Could not pull image {image_tag}: {e}")
                            image_tag = None
                else:
                    logger.warning(f"Service {service.name} has no running container and no docker_image set.")

            if image_tag and not backup.db_only:
                metadata['docker_image'] = image_tag
                logger.info(f"Saving image {image_tag} to {image_filename}...")
                try:
                    image_obj = self.docker_client.images.get(image_tag)
                    with open(image_path, 'wb') as f:
                        for chunk in image_obj.save():
                            f.write(chunk)
                    logger.info(f"Image saved: {os.path.getsize(image_path)} bytes")
                except Exception as img_err:
                    logger.error(f"Failed to save image {image_tag}: {img_err}")
                    # Remove partial file if it exists
                    if os.path.exists(image_path):
                        os.remove(image_path)
                    raise RuntimeError(f"Failed to save image {image_tag}: {img_err}") from img_err

            # 2. Database dump (if container runs a DB — ensures consistent data)
            container_name = service.name
            _dump_container_database(container_name, image_tag, temp_dir)

            # 3. Backup Volumes (Skip if db_only)
            volumes = Volume.objects.filter(service=service)
            for vol in volumes:
                if backup.db_only:
                    continue
                safe_vol_name = vol.name.replace('/', '_').replace('\\', '_').replace('..', '_')
                vol_filename = f"volume_{safe_vol_name}.tar.gz"
                vol_path = os.path.join(temp_dir, vol_filename)

                logger.info(f"Backing up volume {vol.name}...")
                try:
                    # Run a helper container to stream tar output
                    # We mount the source volume at /source and the temp dir at /backup
                    # But we can't easily mount the host temp_dir if we are in a container (DIND).
                    # A better way usually:
                    # container = client.containers.run(..., volumes={vol.name: {'bind': '/data'}}, command="tar -czf - -C /data .")
                    # and read logs/stream.

                    # Note: Using stream=True with logs() or attach() is tricky.
                    # Let's try mounting the volume and just reading the tar stream.

                    # Create a dummy container mounting the volume
                    # We use a busybox image

                    # Check if volume exists in docker
                    try:
                        self.docker_client.volumes.get(vol.name)
                    except docker.errors.NotFound:
                        raise RuntimeError(
                            f"Docker volume {vol.name} is configured for service "
                            f"{service.name} but does not exist on the host"
                        )

                    # Stream tar content directly from a helper container
                    # We run 'tar cf - .' inside the volume
                    stream_container = self.docker_client.containers.run(
                        "alpine:latest",
                        command=["tar", "-czf", "-", "-C", "/volume_data", "."],
                        volumes={vol.name: {'bind': '/volume_data', 'mode': 'ro'}},
                        detach=True,
                        remove=False  # We remove manually after reading
                    )

                    try:
                        # Read the stream
                        with open(vol_path, 'wb') as f:
                            for chunk in stream_container.logs(stream=True, stdout=True, stderr=False):
                                f.write(chunk)

                        metadata['volumes'].append({
                            'name': vol.name,
                            'mount_path': vol.mount_path,
                            'filename': vol_filename,
                            'size_gb': vol.size_gb
                        })
                    finally:
                        stream_container.remove(force=True)

                except Exception as ve:
                    logger.error(f"Failed to backup volume {vol.name}: {ve}")
                    raise RuntimeError(f"Failed to backup volume {vol.name}: {ve}") from ve

            # 3. Create Final Archive
            safe_name = slugify(service.name) or f"service-{str(service.id)[:8]}"
            filename = f"backup_{safe_name}_{uuid.uuid4().hex[:8]}.tar.gz"
            filepath = os.path.join(backups_dir, filename)

            # Add metadata.json
            with open(os.path.join(temp_dir, "metadata.json"), 'w') as f:
                json.dump(metadata, f, indent=2)

            with tarfile.open(filepath, "w:gz") as tar:
                tar.add(temp_dir, arcname="")

            filepath = self._maybe_encrypt(filepath)

            backup.file_path = filepath
            backup.metadata = metadata
            backup.size_bytes = os.path.getsize(filepath)
            BackupService.stamp_encryption_header_into_metadata(backup.metadata, filepath)

            # Enforce maximum backup size
            try:
                max_bytes = int(os.environ.get("BACKUP_MAX_SIZE_BYTES", str(_DEFAULT_MAX_BACKUP_SIZE)))
            except (TypeError, ValueError):
                max_bytes = _DEFAULT_MAX_BACKUP_SIZE
            if max_bytes > 0 and backup.size_bytes > max_bytes:
                os.remove(filepath)
                raise RuntimeError(
                    f"Backup size ({backup.size_bytes} bytes) exceeds maximum "
                    f"({max_bytes} bytes). Increase BACKUP_MAX_SIZE_BYTES or "
                    "reduce service volume/content size."
                )

            backup.status = 'COMPLETED'
            sha = hashlib.sha256()
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha.update(chunk)
            backup.metadata['checksum_sha256'] = sha.hexdigest()
            backup.completed_at = timezone.now()
            backup.save()
            self._prune_old_backups(ServiceBackup, service_id=service.id)

            # Upload to S3 if a cloud destination is configured.
            # Cloud failure is non-fatal — backup stays local and logs an alert.
            cloud_result = _upload_backup_to_cloud(backup, filepath, service.name)
            if not cloud_result["uploaded"] and cloud_result["reason"]:
                backup.metadata["cloud_upload_error"] = cloud_result["reason"]
                backup.save(update_fields=['metadata'])
                _alert_cloud_upload_failed(backup, cloud_result)

            # Clean up temp image if we created one
            if image_tag and image_tag.startswith("backup/"):
                try:
                    self.docker_client.images.remove(image_tag, force=True)
                except Exception as cleanup_err:
                    logger.warning("Failed to clean up temp image %s: %s", image_tag, cleanup_err)

            return backup

        except Exception as e:
            backup.status = 'FAILED'
            backup.error_message = str(e)
            backup.save()
            logger.error(f"Backup failed for service {service.name}: {e}")
            raise
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def restore_service(self, backup_id, target_service_id=None, requesting_user_id=None, raise_on_snapshot_failure=False):
        """
        Restore a service from backup.
        If target_service_id is provided, restore into that service (overwrite).
        Otherwise, restore into the original service.
        If raise_on_snapshot_failure is True, the function raises when the
        pre-restore safety snapshot cannot be created. By default the failure
        is logged but the restore continues, for backward compatibility.
        """
        backup_qs = ServiceBackup.objects.select_related('service', 'service__owner')
        if requesting_user_id is not None:
            backup_qs = backup_qs.filter(service__owner_id=requesting_user_id)
        backup = backup_qs.get(id=backup_id)
        if backup.status != 'COMPLETED':
            raise ValueError("Only COMPLETED backups can be restored.")

        if not target_service_id:
            target_service = backup.service
        else:
            target_qs = Service.objects.filter(id=target_service_id)
            if requesting_user_id is not None:
                target_qs = target_qs.filter(owner_id=requesting_user_id)
            target_service = target_qs.first()
            if not target_service:
                raise PermissionDenied("Target service does not belong to requesting user")

        logger.info(f"Restoring backup {backup.id} to service {target_service.name}")

        # Check if the target server is remote or local
        from django.db.models import Q
        server_obj = getattr(target_service, 'server', None)
        if not server_obj:
            provider = getattr(target_service, 'provider', None)
            if provider and provider.provider_type in ('REMOTE', 'LITE_AGENT'):
                from apps.deployments.models_core import ManagedServer
                host = provider.host or getattr(provider, 'api_url', None)
                if host:
                    server_obj = ManagedServer.objects.filter(
                        Q(host=host) | Q(private_ip=host)
                    ).first()

        is_remote = server_obj is not None and not server_obj.is_primary

        # Create pre-restore safety snapshot BEFORE stopping the container
        # so the running service can be backed up while it's still alive.
        logger.info(f"Creating pre-restore snapshot for service {target_service.name}")
        try:
            self.backup_service(target_service.id, backup_type='PRE_TRANSFER')
        except Exception as e:
            logger.warning(f"Failed to create pre-restore snapshot: {e}")
            if raise_on_snapshot_failure:
                raise RuntimeError(
                    f"Pre-restore snapshot failed: {e}. Refusing to restore "
                    "because the active state would be lost on a corrupt restore."
                ) from e

        # Stop the running service to prevent data corruption during volume restore
        _stop_service_for_restore(target_service, is_remote)

        if is_remote:
            archive_path, cleanup_archive = self._prepare_archive_for_restore(backup)
            temp_dir = os.path.join(os.path.dirname(archive_path), f"restore_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
            return self._restore_remote_service(backup, target_service, server_obj, temp_dir, archive_path, cleanup_archive)

        archive_path, cleanup_archive = self._prepare_archive_for_restore(backup)
        temp_dir = os.path.join(os.path.dirname(archive_path), f"restore_{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # 1. Extract Archive
            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_tar_extractall(tar, temp_dir)

            with open(os.path.join(temp_dir, "metadata.json")) as f:
                metadata = json.load(f)

            # 2. Restore Env Vars
            if 'env_vars' in metadata:
                _fernet_prefix = b'gAAAAAB'
                for env in metadata['env_vars']:
                    value = env.get('value', '')
                    # Detect double-encrypted values from corrupted pre-fix backups.
                    # If value already starts with the Fernet token prefix, it was
                    # raw encrypted bytes from a broken .values() backup. Write it
                    # directly to avoid re-encrypting again.
                    if isinstance(value, str) and value.startswith('gAAAAAB'):
                        from django.db import connection
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "INSERT INTO deployments_environmentvariable (service_id, key, value, is_secret, created_at, updated_at) "
                                "VALUES (%s, %s, %s, %s, NOW(), NOW()) "
                                "ON CONFLICT (service_id, key) DO UPDATE SET value = EXCLUDED.value, is_secret = EXCLUDED.is_secret, updated_at = NOW()",
                                [str(target_service.id), env['key'], value, env.get('is_secret', False)]
                            )
                    else:
                        EnvironmentVariable.objects.update_or_create(
                            service=target_service,
                            key=env['key'],
                            defaults={'value': value, 'is_secret': env.get('is_secret', False)}
                        )

            # 3. Load Docker Image
            restored_image = target_service.docker_image
            image_path = os.path.join(temp_dir, "image.tar")
            if os.path.exists(image_path):
                logger.info("Loading docker image...")
                with open(image_path, 'rb') as f:
                    # load() returns a list of images, we take the first one's tags
                    images = self.docker_client.images.load(f)
                    if images:
                        loaded_image = images[0]
                        restored_image = None
                        if loaded_image.tags:
                            restored_image = loaded_image.tags[0]
                        elif metadata.get('docker_image'):
                            restored_image = metadata['docker_image']
                            repo, tag = self._split_image_reference(restored_image)
                            loaded_image.tag(repository=repo, tag=tag)

                        if restored_image:
                            target_service.docker_image = restored_image
                            # Only switch deploy_type if the service has no
                            # explicit deploy configuration yet. Preserve
                            # COMPOSE/FUNCTION/UPLOAD modes set by the owner.
                            if target_service.deploy_type not in ('COMPOSE', 'FUNCTION', 'UPLOAD', 'GIT'):
                                target_service.deploy_type = 'DOCKER'
                            target_service.save()

            # 4. Restore Volumes
            # Ensure volumes exist in DB
            if 'volumes' in metadata:
                for vol_meta in metadata['volumes']:
                    # We map volume names. If restoring to SAME service, reuse name.
                    # If restoring to DIFFERENT service, we might need to prefix?
                    # Ideally, volumes are tied to Service ID or Name in their Docker name.
                    # But here we just assume the Volume object name is the Docker volume name.

                    # Find or Create Volume object
                    vol_obj, _ = Volume.objects.get_or_create(
                        service=target_service,
                        mount_path=vol_meta['mount_path'],
                        defaults={
                            'name': vol_meta['name'], # Use original name? Might conflict if on same host!
                            'size_gb': vol_meta.get('size_gb', 1)
                        }
                    )

                    # If we are restoring to a NEW service or if we want to ensure isolation,
                    # we should probably ensure the docker volume name is unique to the target service.
                    # But for now, let's assume we overwrite the volume content if it exists.

                    # Ensure Docker volume exists
                    try:
                        self.docker_client.volumes.get(vol_obj.name)
                    except docker.errors.NotFound:
                        self.docker_client.volumes.create(name=vol_obj.name)

                    # Restore data
                    vol_tar_path = os.path.join(temp_dir, vol_meta['filename'])
                    if os.path.exists(vol_tar_path):
                        logger.info(f"Restoring volume {vol_obj.name}...")

                        # Use docker-py to restore data instead of subprocess
                        # 1. Start a temporary helper container
                        helper = self.docker_client.containers.run(
                            "alpine:latest",
                            command=["sleep", "86400"],
                            volumes={vol_obj.name: {'bind': '/dest', 'mode': 'rw'}},
                            detach=True,
                            remove=True
                        )

                        try:
                            # Docker put_archive expects an uncompressed tar
                            # stream, so wrap the saved .tar.gz as a file in a
                            # plain tar, upload it, then extract it in-container.
                            upload_tar_path = os.path.join(
                                temp_dir,
                                f"upload_{uuid.uuid4().hex}.tar",
                            )
                            with tarfile.open(upload_tar_path, "w") as upload_tar:
                                upload_tar.add(
                                    vol_tar_path,
                                    arcname=vol_meta['filename'],
                                )
                            with open(upload_tar_path, 'rb') as upload_file:
                                self.docker_client.api.put_archive(
                                    helper.id,
                                    "/tmp",
                                    upload_file,
                                )
                            result = helper.exec_run([
                                "sh",
                                "-c",
                                f"tar -xzf /tmp/{vol_meta['filename']} -C /dest",
                            ])
                            if getattr(result, "exit_code", 1) != 0:
                                raise RuntimeError(
                                    f"Failed to extract volume {vol_obj.name}: "
                                    f"{getattr(result, 'output', b'')!r}"
                                )
                            helper.exec_run(["rm", f"/tmp/{vol_meta['filename']}"])
                        finally:
                            helper.remove(force=True)

            # 5. Restore database from SQL dump if present (safety net for services that run a DB)
            db_dump = os.path.join(temp_dir, 'db_dump.sql')
            redis_dump = os.path.join(temp_dir, 'redis_dump.rdb')
            image_lower = (restored_image or '').lower()
            
            vol_binds = {}
            for vol_meta in metadata.get('volumes', []):
                vol_obj = Volume.objects.filter(
                    service=target_service, mount_path=vol_meta['mount_path']
                ).first()
                if vol_obj:
                    vol_binds[vol_obj.name] = {'bind': vol_meta['mount_path'], 'mode': 'rw'}

            if os.path.exists(redis_dump) and os.path.getsize(redis_dump) > 0 and 'redis' in image_lower:
                logger.info("Restoring Redis RDB dump...")
                try:
                    # For Redis, we just copy the RDB file into the data volume.
                    # We assume the volume is bound to /data.
                    temp_ctr = self.docker_client.containers.run(
                        "alpine:latest",
                        command=["sleep", "60"],
                        volumes=vol_binds,
                        detach=True,
                        remove=True,
                    )
                    try:
                        _copy_file_to_container(
                            self.docker_client, temp_ctr.id,
                            redis_dump, '/tmp/dump.rdb',
                        )
                        # Copy from tmp to whatever volume is mounted.
                        temp_ctr.exec_run(['sh', '-c', 'cp /tmp/dump.rdb /data/dump.rdb || true'])
                        logger.info("Redis RDB dump restored successfully.")
                    finally:
                        temp_ctr.remove(force=True)
                except Exception as db_restore_err:
                    raise RuntimeError(f"Redis dump restore failed: {db_restore_err}") from db_restore_err

            elif os.path.exists(db_dump) and os.path.getsize(db_dump) > 0 and restored_image:
                logger.info("Restoring database from SQL dump...")
                try:
                    import time
                    
                    if 'postgres' in image_lower:
                        db_user = 'postgres'
                        db_name = 'postgres'
                        db_password = os.environ.get('POSTGRES_PASSWORD', '')
                        for ev in metadata.get('env_vars', []):
                            if ev['key'] == 'POSTGRES_USER':
                                db_user = ev['value']
                            elif ev['key'] == 'POSTGRES_DB':
                                db_name = ev['value']
                            elif ev['key'] == 'POSTGRES_PASSWORD':
                                db_password = ev['value']
                        
                        temp_ctr = self.docker_client.containers.run(
                            restored_image,
                            volumes=vol_binds,
                            detach=True,
                            remove=True,
                            environment={
                                'POSTGRES_USER': db_user,
                                'POSTGRES_DB': db_name,
                                'POSTGRES_PASSWORD': db_password,
                            }
                        )
                        try:
                            for _ in range(30):
                                res = temp_ctr.exec_run(['pg_isready', '-U', db_user])
                                if res.exit_code == 0:
                                    break
                                time.sleep(1)
                            else:
                                raise RuntimeError("Postgres failed to start in time for restore.")

                            _copy_file_to_container(
                                self.docker_client, temp_ctr.id,
                                db_dump, '/tmp/db_dump.sql',
                            )
                            res = temp_ctr.exec_run(
                                ['psql', '-U', db_user, '-d', db_name,
                                 '-v', 'ON_ERROR_STOP=1',
                                 '--single-transaction',
                                 '-f', '/tmp/db_dump.sql'],
                                environment={'PGPASSWORD': db_password},
                            )
                            if res.exit_code != 0:
                                raise RuntimeError(f"psql execution failed: {res.output}")
                            logger.info("Postgres SQL dump restored successfully.")
                        finally:
                            temp_ctr.remove(force=True)
                            
                    elif 'mysql' in image_lower or 'mariadb' in image_lower:
                        db_password = ''
                        for ev in metadata.get('env_vars', []):
                            if ev['key'] == 'MYSQL_ROOT_PASSWORD':
                                db_password = ev['value']
                            elif ev['key'] == 'MYSQL_PASSWORD' and not db_password:
                                db_password = ev['value']
                                
                        temp_ctr = self.docker_client.containers.run(
                            restored_image,
                            volumes=vol_binds,
                            detach=True,
                            remove=True,
                            environment={
                                'MYSQL_ROOT_PASSWORD': db_password,
                                'MYSQL_ALLOW_EMPTY_PASSWORD': 'yes' if not db_password else 'no',
                            }
                        )
                        try:
                            for _ in range(45):
                                res = temp_ctr.exec_run(
                                    ['mysqladmin', 'ping', '-h', '127.0.0.1', '-uroot'],
                                    environment={'MYSQL_PWD': db_password}
                                )
                                if res.exit_code == 0:
                                    break
                                time.sleep(1)
                            else:
                                raise RuntimeError("MySQL/MariaDB failed to start in time for restore.")

                            _copy_file_to_container(
                                self.docker_client, temp_ctr.id,
                                db_dump, '/tmp/db_dump.sql',
                            )
                            # Using sh -c for mysql import
                            res = temp_ctr.exec_run(
                                ['sh', '-c', 'mysql -uroot < /tmp/db_dump.sql'],
                                environment={'MYSQL_PWD': db_password}
                            )
                            if res.exit_code != 0:
                                raise RuntimeError(f"mysql execution failed: {res.output}")
                            logger.info("MySQL/MariaDB SQL dump restored successfully.")
                        finally:
                            temp_ctr.remove(force=True)
                    else:
                        logger.warning(f"Unknown database image {restored_image} for db_dump.sql. Skipping.")
                except Exception as db_restore_err:
                    raise RuntimeError(f"Database SQL dump restore failed: {db_restore_err}") from db_restore_err

            logger.info("Restore complete. Queueing deployment.")
            from apps.deployments.models import Deployment
            from apps.deployments.tasks_deploy import (
                _resolve_provider_for_service,
                enqueue_smart_deploy_task,
            )

            # Restore source-code metadata so the deployment pulls the
            # right repository and branch — critical for cross-server
            # and cross-master restores.
            restore_repo = metadata.get('git_url', '') or metadata.get('repository_url', '')
            restore_branch = metadata.get('branch', '') or target_service.branch or 'main'
            restore_buildpack = metadata.get('buildpack', '') or target_service.buildpack
            if restore_repo:
                target_service.repository_url = restore_repo
            if restore_branch:
                target_service.branch = restore_branch
            if restore_buildpack and target_service.buildpack == 'NIXPACKS':
                target_service.buildpack = restore_buildpack
            if metadata.get('deploy_type') and target_service.deploy_type == 'GIT':
                target_service.deploy_type = metadata['deploy_type']

            provider = _resolve_provider_for_service(target_service, prefer_local=True)
            if provider:
                branch_ref = restore_branch or 'main'
                deployment = Deployment.objects.create(
                    service=target_service,
                    status=Deployment.Status.QUEUED,
                    commit_hash=branch_ref,
                    commit_message=f"Restored from backup {backup.id} (branch: {branch_ref})",
                )

                target_service.status = Service.Status.ACTIVE

                # ── Cross-platform restore: remap domain ──────────────
                _remap_domain_on_restore(target_service, metadata)

                target_service.save()

                enqueue_smart_deploy_task(
                    deployment_id=str(deployment.id),
                    provider_id=str(provider.id),
                    skip_review=True
                )

                # Force the deployment to become immediately "latest" by clearing any cached status
                # and ensuring immediate processing
                from django.core.cache import cache
                cache.delete(f'service_{target_service.id}_latest_deployment')

                # Also update the service's active_target metadata to reflect the restored state
                target_service.active_target_type = 'local'  # Assuming restored services run locally
                target_service.save()

            else:
                logger.warning(f"Could not resolve provider to queue deployment for restored remote service {target_service.id}")
                target_service.status = Service.Status.ACTIVE
                target_service.save()
                _emergency_restart_remote_container(target_service, server)
                # Emergency restart: volumes are restored but no deploy was
                # queued. Try to restart the stopped container directly so
                # the service isn't left dead.
                _emergency_restart_container(target_service)

            return True

        except Exception as e:
            logger.error(f"Restore failed: {e}")
            raise
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            if cleanup_archive and os.path.exists(cleanup_archive):
                os.remove(cleanup_archive)

    def _backup_remote_service(self, service, backup, server, include_secret_values) -> ServiceBackup:
        """Perform backup of a service running on a remote/lite-agent node via SSH."""
        logger.info(f"Starting remote backup of service {service.name} on server {server.name}")

        # Instantiate SSHClient
        from apps.deployments.services.ssh_client import SSHClient
        ssh = SSHClient(
            ip=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            private_key=server.ssh_key,
            password=server.ssh_password,
            wg_address=server.wg_address,
        )
        ssh.connect()

        # Check if Docker is running on remote server
        if not ssh.check_docker():
            raise RuntimeError(f"Docker is not available on remote server {server.name}")

        temp_dir = None
        remote_temp_dir = f"/tmp/backup_tmp_{uuid.uuid4().hex[:8]}"
        image_tag = f"backup/{slugify(service.name)}:{uuid.uuid4().hex[:8]}"
        remote_image_path = f"{remote_temp_dir}/image.tar"
        remote_archive_path = f"/tmp/backup_{slugify(service.name)}_{uuid.uuid4().hex[:8]}.tar.gz"

        try:
            # 1. Create remote temp directory
            ssh.exec_command(f"mkdir -p {remote_temp_dir}", raise_on_error=True)

            # 2. Backup Docker Image remotely
            # Try to commit container
            import shlex
            safe_service_name = shlex.quote(service.name)
            out, err, code = ssh.exec_command(f"docker commit {safe_service_name} {image_tag}")
            has_image = False
            if code == 0:
                logger.info(f"Committed remote container {service.name} to {image_tag}")
                # Save committed image to tar
                logger.info(f"Saving remote image {image_tag}...")
                out, err, code = ssh.exec_command(f"docker save -o {remote_image_path} {image_tag}")
                if code == 0:
                    has_image = True
                else:
                    raise RuntimeError(f"Failed to save remote image: {err or out}")
            # If not running, fall back to configured docker_image
            elif service.docker_image:
                image_tag = service.docker_image
                # Try to pull it on remote if not exists
                ssh.exec_command(f"docker image inspect {image_tag} || docker pull {image_tag}")
                logger.info(f"Saving remote image {image_tag}...")
                out, err, code = ssh.exec_command(f"docker save -o {remote_image_path} {image_tag}")
                if code == 0:
                    has_image = True
                else:
                    raise RuntimeError(f"Failed to save remote image: {err or out}")

            # 3. Backup Volumes remotely
            volumes = Volume.objects.filter(service=service)
            volumes_meta = []
            for vol in volumes:
                # Check if volume exists remotely
                out, err, code = ssh.exec_command(f"docker volume inspect {shlex.quote(vol.name)}")
                if code != 0:
                    logger.warning(f"Docker volume {vol.name} not found on remote server, skipping.")
                    continue

                safe_vol_name = vol.name.replace('/', '_').replace('\\', '_').replace('..', '_')
                vol_filename = f"volume_{safe_vol_name}.tar.gz"
                logger.info(f"Backing up remote volume {vol.name}...")

                # Compress remote volume using alpine helper container
                compress_cmd = (
                    f"docker run --rm "
                    f"--security-opt no-new-privileges:true --security-opt apparmor=docker-default "
                    f"-v {shlex.quote(vol.name)}:/volume_data:ro "
                    f"-v {remote_temp_dir}:/backup alpine:latest "
                    f"tar -czf /backup/{shlex.quote(vol_filename)} -C /volume_data ."
                )
                out, err, code = ssh.exec_command(compress_cmd)
                if code == 0:
                    volumes_meta.append({
                        'name': vol.name,
                        'mount_path': vol.mount_path,
                        'filename': vol_filename,
                        'size_gb': vol.size_gb
                    })
                else:
                    raise RuntimeError(f"Failed to backup remote volume {vol.name}: {err or out}")

            # 3b. Database dump — run the appropriate dump command inside
            # the remote container via SSH (mirrors _dump_container_database).
            image_lower = (service.docker_image or '').lower()
            if 'postgres' in image_lower:
                c_env = {}
                out, _, _ = ssh.exec_command(
                    f"docker inspect -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' {safe_service_name}",
                    raise_on_error=False,
                )
                for line in (out or '').splitlines():
                    if '=' in line:
                        k, v = line.split('=', 1)
                        c_env[k] = v
                pg_user = c_env.get('POSTGRES_USER', 'smsly_admin')
                pg_db = c_env.get('POSTGRES_DB', 'postgres')
                pg_password = c_env.get('POSTGRES_PASSWORD', '')
                dump_cmd = (
                    f"docker exec -e PGPASSWORD={shlex.quote(pg_password)} "
                    f"{safe_service_name} pg_dump -U {shlex.quote(pg_user)} "
                    f"-d {shlex.quote(pg_db)} --lock-wait-timeout=5000 "
                    f"--clean --if-exists --no-owner --no-acl "
                    f"> {remote_temp_dir}/db_dump.sql"
                )
                _, _, code = ssh.exec_command(dump_cmd)
                if code != 0:
                    logger.warning("Remote pg_dump failed — DB data will not be in backup")
            elif 'mysql' in image_lower or 'mariadb' in image_lower:
                c_env = {}
                out, _, _ = ssh.exec_command(
                    f"docker inspect -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}' {safe_service_name}",
                    raise_on_error=False,
                )
                for line in (out or '').splitlines():
                    if '=' in line:
                        k, v = line.split('=', 1)
                        c_env[k] = v
                mysql_pass = c_env.get('MYSQL_ROOT_PASSWORD', c_env.get('MYSQL_PASSWORD', ''))
                dump_cmd = (
                    f"docker exec -e MYSQL_PWD={shlex.quote(mysql_pass)} "
                    f"{safe_service_name} mysqldump --all-databases -u root "
                    f"> {remote_temp_dir}/db_dump.sql"
                )
                _, _, code = ssh.exec_command(dump_cmd)
                if code != 0:
                    logger.warning("Remote mysqldump failed — DB data will not be in backup")
            elif 'redis' in image_lower:
                ssh.exec_command(f"docker exec {safe_service_name} redis-cli SAVE")
                out, _, code = ssh.exec_command(
                    f"docker cp {safe_service_name}:/data/dump.rdb {remote_temp_dir}/redis_dump.rdb"
                )
                if code != 0:
                    logger.warning("Remote Redis SAVE/dump.rdb copy failed")

            # 4. Prepare Metadata.json locally and upload it
            env_vars_raw = [
                {"key": ev.key, "value": ev.value, "is_secret": ev.is_secret}
                for ev in EnvironmentVariable.objects.filter(service=service).only('key', 'value', 'is_secret')
            ]
            env_vars = []
            for ev in env_vars_raw:
                entry = dict(ev)
                if entry.get('is_secret') and not include_secret_values:
                    entry['value'] = '********'
                env_vars.append(entry)

            metadata = {
                'service_name': service.name,
                'service_id': str(service.id),
                'deploy_type': service.deploy_type,
                'buildpack': service.buildpack,
                'env_vars': env_vars,
                'secrets_included': include_secret_values,
                'git_url': service.repository_url,
                'branch': service.branch,
                'created_at': str(timezone.now()),
                'volumes': volumes_meta
            }
            if has_image:
                metadata['docker_image'] = image_tag

            # Write metadata to a local temp file
            backups_dir = self._get_backups_dir('services')
            temp_dir = os.path.join(backups_dir, f"tmp_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
            local_metadata_path = os.path.join(temp_dir, "metadata.json")
            with open(local_metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)

            # Upload metadata.json to remote temp dir
            ssh.upload_file(local_metadata_path, f"{remote_temp_dir}/metadata.json")

            # 5. Archive remotely
            logger.info("Packaging remote backup archive...")
            archive_cmd = f"tar -czf {remote_archive_path} -C {remote_temp_dir} ."
            ssh.exec_command(archive_cmd, raise_on_error=True)

            # 6. Download final archive to local control plane
            safe_name = slugify(service.name) or f"service-{str(service.id)[:8]}"
            local_filename = f"backup_{safe_name}_{uuid.uuid4().hex[:8]}.tar.gz"
            local_filepath = os.path.join(backups_dir, local_filename)

            logger.info("Downloading remote backup archive to local control plane...")
            ssh.download_file(remote_archive_path, local_filepath)

            # Optional encryption
            local_filepath = self._maybe_encrypt(local_filepath)

            # Save backup details
            backup.file_path = local_filepath
            backup.metadata = metadata
            backup.status = 'COMPLETED'
            backup.size_bytes = os.path.getsize(local_filepath)
            backup.completed_at = timezone.now()
            BackupService.stamp_encryption_header_into_metadata(backup.metadata, local_filepath)
            backup.save()

            self._prune_old_backups(ServiceBackup, service_id=service.id)
            return backup

        finally:
            # Clean up remote temp files and images
            logger.info("Cleaning up remote temp files...")
            ssh.exec_command(f"rm -rf {remote_temp_dir} {remote_archive_path}")
            if image_tag.startswith("backup/"):
                ssh.exec_command(f"docker rmi -f {image_tag}")

            # Clean up local temp files
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            ssh.close()

    def _restore_remote_service(self, backup, target_service, server, temp_dir, archive_path, cleanup_archive):
        """Restore a service backup to a remote server via SSH."""
        logger.info(f"Starting remote restore of backup {backup.id} to service {target_service.name} on server {server.name}")

        from apps.deployments.services.ssh_client import SSHClient
        ssh = SSHClient(
            ip=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            private_key=server.ssh_key,
            password=server.ssh_password,
            wg_address=server.wg_address,
        )
        try:
            ssh.connect()

            if not ssh.check_docker():
                raise RuntimeError(f"Docker is not available on remote server {server.name}")

            # 1. Extract Archive locally to read metadata
            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_tar_extractall(tar, temp_dir)

            with open(os.path.join(temp_dir, "metadata.json")) as f:
                metadata = json.load(f)

            # 2. Restore Env Vars (always local in the database)
            if 'env_vars' in metadata:
                for env in metadata['env_vars']:
                    value = env.get('value', '')
                    if isinstance(value, str) and value.startswith('gAAAAAB'):
                        from django.db import connection
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "INSERT INTO deployments_environmentvariable (service_id, key, value, is_secret, created_at, updated_at) "
                                "VALUES (%s, %s, %s, %s, NOW(), NOW()) "
                                "ON CONFLICT (service_id, key) DO UPDATE SET value = EXCLUDED.value, is_secret = EXCLUDED.is_secret, updated_at = NOW()",
                                [str(target_service.id), env['key'], value, env.get('is_secret', False)]
                            )
                    else:
                        EnvironmentVariable.objects.update_or_create(
                            service=target_service,
                            key=env['key'],
                            defaults={'value': value, 'is_secret': env.get('is_secret', False)}
                        )

            # 3. Load Docker Image remotely
            restored_image = target_service.docker_image
            image_path = os.path.join(temp_dir, "image.tar")
            if os.path.exists(image_path):
                logger.info("Uploading Docker image to remote server...")
                remote_image_path = f"/tmp/image_{uuid.uuid4().hex[:8]}.tar"
                ssh.upload_file(image_path, remote_image_path)

                logger.info("Loading Docker image remotely...")
                out, err, code = ssh.exec_command(f"docker load -i {shlex.quote(remote_image_path)}")
                ssh.exec_command(f"rm -f {shlex.quote(remote_image_path)}") # clean up immediately
                if code != 0:
                    raise RuntimeError(f"Failed to load Docker image remotely: {err or out}")

                if metadata.get('docker_image'):
                    restored_image = metadata['docker_image']
                    target_service.docker_image = restored_image
                    if target_service.deploy_type not in ('COMPOSE', 'FUNCTION', 'UPLOAD', 'GIT'):
                        target_service.deploy_type = 'DOCKER'
                    target_service.save()

            # 4. Restore Volumes remotely
            if 'volumes' in metadata:
                for vol_meta in metadata['volumes']:
                    vol_obj, _ = Volume.objects.get_or_create(
                        service=target_service,
                        mount_path=vol_meta['mount_path'],
                        defaults={
                            'name': vol_meta['name'],
                            'size_gb': vol_meta.get('size_gb', 1)
                        }
                    )

                    # Ensure remote volume exists
                    ssh.exec_command(f"docker volume create {shlex.quote(vol_obj.name)}")

                    vol_tar_path = os.path.join(temp_dir, vol_meta['filename'])
                    if os.path.exists(vol_tar_path):
                        logger.info(f"Uploading volume archive {vol_meta['filename']} to remote...")
                        remote_vol_tar_path = f"/tmp/{vol_meta['filename']}"
                        ssh.upload_file(vol_tar_path, remote_vol_tar_path)

                        logger.info(f"Extracting volume {vol_obj.name} remotely...")
                        # Run helper container remotely to extract
                        extract_cmd = (
                            f"docker run --rm "
                            f"--security-opt no-new-privileges:true --security-opt apparmor=docker-default "
                            f"-v {shlex.quote(vol_obj.name)}:/dest "
                            f"-v /tmp:/src alpine:latest "
                            f"tar -xzf /src/{shlex.quote(vol_meta['filename'])} -C /dest"
                        )
                        out, err, code = ssh.exec_command(extract_cmd)
                        ssh.exec_command(f"rm -f {shlex.quote(remote_vol_tar_path)}") # clean up
                        if code != 0:
                            raise RuntimeError(f"Failed to extract volume remotely: {err or out}")

            # 5. Restore database from SQL dump if present (safety net for services that run a DB)
            db_dump = os.path.join(temp_dir, 'db_dump.sql')
            redis_dump = os.path.join(temp_dir, 'redis_dump.rdb')
            image_lower = (restored_image or '').lower()
            
            vol_binds = []
            for vol_meta in metadata.get('volumes', []):
                vol_obj = Volume.objects.filter(
                    service=target_service, mount_path=vol_meta['mount_path']
                ).first()
                if vol_obj:
                    vol_binds.append(f"-v {shlex.quote(vol_obj.name)}:{shlex.quote(vol_meta['mount_path'])}")
            vol_bind_str = " ".join(vol_binds)

            if os.path.exists(redis_dump) and os.path.getsize(redis_dump) > 0 and 'redis' in image_lower:
                logger.info("Uploading Redis RDB dump to remote server...")
                remote_redis_dump = f"/tmp/redis_dump_{uuid.uuid4().hex[:8]}.rdb"
                ssh.upload_file(redis_dump, remote_redis_dump)
                
                try:
                    logger.info("Restoring Redis RDB dump remotely...")
                    start_cmd = f"docker run -d --rm {vol_bind_str} alpine:latest sleep 60"
                    out, err, code = ssh.exec_command(start_cmd)
                    if code != 0:
                        raise RuntimeError(f"Failed to start temp container remotely: {err or out}")
                    temp_ctr_id = out.strip()
                    
                    try:
                        ssh.exec_command(f"docker cp {shlex.quote(remote_redis_dump)} {temp_ctr_id}:/tmp/dump.rdb", timeout=300)
                        ssh.exec_command(f"docker exec {temp_ctr_id} sh -c 'cp /tmp/dump.rdb /data/dump.rdb || true'")
                        logger.info("Remote Redis RDB dump restored successfully.")
                    finally:
                        ssh.exec_command(f"docker kill {temp_ctr_id}")
                finally:
                    ssh.exec_command(f"rm -f {shlex.quote(remote_redis_dump)}")

            elif os.path.exists(db_dump) and os.path.getsize(db_dump) > 0 and restored_image:
                logger.info("Uploading database SQL dump to remote server...")
                remote_db_dump = f"/tmp/db_dump_{uuid.uuid4().hex[:8]}.sql"
                ssh.upload_file(db_dump, remote_db_dump)
                
                try:
                    logger.info("Restoring database from SQL dump remotely...")
                    if 'postgres' in image_lower:
                        db_user = 'postgres'
                        db_name = 'postgres'
                        db_password = ''
                        for ev in metadata.get('env_vars', []):
                            if ev['key'] == 'POSTGRES_USER':
                                db_user = ev['value']
                            elif ev['key'] == 'POSTGRES_DB':
                                db_name = ev['value']
                            elif ev['key'] == 'POSTGRES_PASSWORD':
                                db_password = ev['value']
                        
                        env_str = f"-e POSTGRES_USER={shlex.quote(db_user)} -e POSTGRES_DB={shlex.quote(db_name)} -e POSTGRES_PASSWORD={shlex.quote(db_password)}"
                        start_cmd = f"docker run -d --rm {env_str} {vol_bind_str} {shlex.quote(restored_image)}"
                        out, err, code = ssh.exec_command(start_cmd)
                        if code != 0:
                            raise RuntimeError(f"Failed to start temp DB container remotely: {err or out}")
                        temp_ctr_id = out.strip()
                        
                        try:
                            wait_cmd = f"docker exec -e PGUSER={shlex.quote(db_user)} {temp_ctr_id} sh -c 'for i in $(seq 1 30); do pg_isready -U \"$PGUSER\" && exit 0; sleep 1; done; exit 1'"
                            out, err, code = ssh.exec_command(wait_cmd)
                            if code != 0:
                                raise RuntimeError(f"Remote Postgres failed to start in time: {err or out}")

                            ssh.exec_command(f"docker cp {shlex.quote(remote_db_dump)} {temp_ctr_id}:/tmp/db_dump.sql", timeout=300)
                            psql_cmd = f"docker exec -e PGPASSWORD={shlex.quote(db_password)} {temp_ctr_id} psql -v ON_ERROR_STOP=1 --single-transaction -U {shlex.quote(db_user)} -d {shlex.quote(db_name)} -f /tmp/db_dump.sql"
                            out, err, code = ssh.exec_command(psql_cmd, timeout=86400)
                            if code != 0:
                                raise RuntimeError(f"psql execution failed: {err or out}")
                            logger.info("Remote Postgres SQL dump restored successfully.")
                        finally:
                            ssh.exec_command(f"docker kill {temp_ctr_id}")
                            
                    elif 'mysql' in image_lower or 'mariadb' in image_lower:
                        db_password = ''
                        for ev in metadata.get('env_vars', []):
                            if ev['key'] == 'MYSQL_ROOT_PASSWORD':
                                db_password = ev['value']
                            elif ev['key'] == 'MYSQL_PASSWORD' and not db_password:
                                db_password = ev['value']
                                
                        allow_empty = 'yes' if not db_password else 'no'
                        env_str = f"-e MYSQL_ROOT_PASSWORD={shlex.quote(db_password)} -e MYSQL_ALLOW_EMPTY_PASSWORD={shlex.quote(allow_empty)}"
                        start_cmd = f"docker run -d --rm {env_str} {vol_bind_str} {shlex.quote(restored_image)}"
                        out, err, code = ssh.exec_command(start_cmd)
                        if code != 0:
                            raise RuntimeError(f"Failed to start temp DB container remotely: {err or out}")
                        temp_ctr_id = out.strip()
                        
                        try:
                            wait_cmd = f"docker exec -e MYSQL_PWD={shlex.quote(db_password)} {temp_ctr_id} sh -c 'for i in $(seq 1 45); do mysqladmin ping -h 127.0.0.1 -uroot && exit 0; sleep 1; done; exit 1'"
                            out, err, code = ssh.exec_command(wait_cmd)
                            if code != 0:
                                raise RuntimeError(f"Remote MySQL/MariaDB failed to start in time: {err or out}")

                            ssh.exec_command(f"docker cp {shlex.quote(remote_db_dump)} {temp_ctr_id}:/tmp/db_dump.sql", timeout=300)
                            mysql_cmd = f"docker exec -e MYSQL_PWD={shlex.quote(db_password)} {temp_ctr_id} sh -c 'mysql -uroot < /tmp/db_dump.sql'"
                            out, err, code = ssh.exec_command(mysql_cmd, timeout=86400)
                            if code != 0:
                                raise RuntimeError(f"mysql execution failed: {err or out}")
                            logger.info("Remote MySQL SQL dump restored successfully.")
                        finally:
                            ssh.exec_command(f"docker kill {temp_ctr_id}")
                    else:
                        logger.warning(f"Unknown database image {restored_image} for db_dump.sql. Skipping.")
                finally:
                    ssh.exec_command(f"rm -f {shlex.quote(remote_db_dump)}")

            logger.info("Remote restore complete. Queueing deployment.")
            from apps.deployments.models import Deployment
            from apps.deployments.tasks_deploy import (
                _resolve_provider_for_service,
                enqueue_smart_deploy_task,
            )

            # Restore source-code metadata on the target service so
            # future redeploys pull the right repo + branch.
            restore_repo = metadata.get('git_url', '') or metadata.get('repository_url', '')
            restore_branch = metadata.get('branch', '') or target_service.branch or 'main'
            restore_buildpack = metadata.get('buildpack', '') or target_service.buildpack
            if restore_repo:
                target_service.repository_url = restore_repo
            if restore_branch:
                target_service.branch = restore_branch
            if restore_buildpack and target_service.buildpack == 'NIXPACKS':
                target_service.buildpack = restore_buildpack
            if metadata.get('deploy_type') and target_service.deploy_type == 'GIT':
                target_service.deploy_type = metadata['deploy_type']

            provider = _resolve_provider_for_service(target_service, prefer_local=False)
            if provider:
                branch_ref = restore_branch or 'main'
                deployment = Deployment.objects.create(
                    service=target_service,
                    status=Deployment.Status.QUEUED,
                    commit_hash=branch_ref,
                    commit_message=f"Restored from backup {backup.id} (branch: {branch_ref})",
                )

                target_service.status = Service.Status.ACTIVE
                target_service.save()

                enqueue_smart_deploy_task(
                    deployment_id=str(deployment.id),
                    provider_id=str(provider.id),
                    skip_review=True
                )

                from django.core.cache import cache
                cache.delete(f'service_{target_service.id}_latest_deployment')

                target_service.active_target_type = target_service.active_target_type or 'remote'
                target_service.save()
            else:
                logger.warning(f"Could not resolve provider to queue deployment for restored service {target_service.id}")
                target_service.status = Service.Status.ACTIVE
                target_service.save()

            return True

        except Exception as e:
            logger.error(f"Remote restore failed: {e}")
            raise
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            if cleanup_archive and os.path.exists(cleanup_archive):
                os.remove(cleanup_archive)
            ssh.close()

    @staticmethod
    def _split_image_reference(image_ref):
        """Split docker image reference into repository and tag."""
        image_ref = str(image_ref or '').strip()
        if not image_ref:
            return '', 'latest'
        last_segment = image_ref.rsplit('/', 1)[-1]
        if ':' in last_segment:
            return image_ref.rsplit(':', 1)
        return image_ref, 'latest'

    def backup_server(self, backup_id=None, db_only=False):
        """
        Full server backup:
        1. PG_DUMP of the database.
        2. Backup of all services (recursive).
        3. Backup of PlatformConfig/Secrets.
        """
        if not self.docker_client:
            raise RuntimeError(
                "Docker is not available. Server backups require a running Docker daemon."
            )
        if backup_id:
            try:
                backup = ServerBackup.objects.get(id=backup_id)
                backup.status = 'IN_PROGRESS'
                backup.save(update_fields=['status'])
            except ServerBackup.DoesNotExist:
                backup = ServerBackup.objects.create(status='IN_PROGRESS', db_only=db_only)
        else:
            backup = ServerBackup.objects.create(status='IN_PROGRESS', db_only=db_only)
        temp_dir = None
        try:
            backups_dir = self._get_backups_dir('server')
            temp_dir = os.path.join(backups_dir, f"tmp_srv_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)

            # 1. Database Dump
            # If DB is in a container, we use docker exec.
            db_file = os.path.join(temp_dir, "db_dump.sql")

            # Find the actual postgres container (not pgcat)
            try:
                db_url = settings.DATABASES['default']
                host = db_url.get('HOST', 'localhost')
                port = db_url.get('PORT', '5432')
                user = db_url.get('USER', 'postgres')
                name = db_url.get('NAME', 'postgres')
                password = db_url.get('PASSWORD', '')

                env = os.environ.copy()
                env['PGPASSWORD'] = password

                # Smart container discovery: find the actual postgres container
                # The DB HOST in settings is often 'pgcat', but pg_dump
                # must run inside the real postgres container.
                pg_container = None
                try:
                    for c in self.docker_client.containers.list():
                        c_name = c.name.lower()
                        c_image = (c.image.tags[0] if c.image.tags else '').lower()
                        # Match containers with 'db' in name and postgres image
                        if ('postgres' in c_image and 'pgcat' not in c_name):
                            pg_container = c
                            break
                        # Fallback: match '-db-' in container name
                        if (('-db-' in c_name or c_name.endswith('-db'))
                                and 'pgcat' not in c_name
                                and 'redis' not in c_name):
                            pg_container = c
                except Exception:
                    pass

                if pg_container:
                    # Read DB name/user from container env if available
                    c_env = {e.split('=', 1)[0]: e.split('=', 1)[1]
                             for e in (pg_container.attrs.get('Config', {})
                                       .get('Env', []))
                             if '=' in e}
                    pg_user = c_env.get('POSTGRES_USER', user)
                    pg_db = c_env.get('POSTGRES_DB', name)

                    cmd = ["pg_dump", "-U", pg_user, "-d", pg_db,
                           "--lock-wait-timeout=5000",
                           "--clean", "--if-exists",
                           "--no-owner", "--no-acl"]
                    res = pg_container.exec_run(cmd, environment={'PGPASSWORD': password})
                    if res.exit_code == 0:
                        with open(db_file, 'wb') as f:
                            f.write(res.output)
                    else:
                        raise Exception(f"pg_dump failed: {res.output}")

                elif host not in ('pgcat', 'localhost', '127.0.0.1'):
                    # Host is a remote address, try direct pg_dump
                    try:
                        self.docker_client.containers.get(host)
                        container = self.docker_client.containers.get(host)
                        res = container.exec_run(
                            ["pg_dump", "-U", user, "-d", name,
                             "--clean", "--if-exists",
                             "--no-owner", "--no-acl"])
                        if res.exit_code == 0:
                            with open(db_file, 'wb') as f:
                                f.write(res.output)
                        else:
                            raise Exception(f"pg_dump failed: {res.output}")
                    except Exception:
                        import subprocess
                        cmd = ["pg_dump", "-h", host, "-p", str(port), "-U", user, name,
                               "--clean", "--if-exists", "--no-owner", "--no-acl"]
                        with open(db_file, 'w') as f:
                            subprocess.run(cmd, env=env, stdout=f, check=True)
                else:
                    # Fallback: run pg_dump locally via pgcat's upstream
                    import subprocess
                    cmd = ["pg_dump", "-h", host, "-p", str(port), "-U", user, name,
                           "--clean", "--if-exists", "--no-owner", "--no-acl"]
                    with open(db_file, 'w') as f:
                        subprocess.run(cmd, env=env, stdout=f, check=True)

            except Exception as e:
                logger.error("Database backup FAILED — aborting server backup: %s", e)
                raise RuntimeError(f"Database dump failed: {e}") from e

            # 1b. Include .env file for configuration recovery (secrets redacted)
            env_source = getattr(settings, 'PLATFORM_ENV_PATH', '/opt/smsly-hosting/.env')
            if os.path.exists(env_source):
                _redact_env_for_backup(env_source, os.path.join(temp_dir, ".env"))

            # 1c. Include SSL certificates if they exist
            cert_dirs = getattr(
                settings, 'PLATFORM_CERT_DIRS',
                ['/opt/smsly-hosting/caddy-config', '/etc/letsencrypt', '/opt/smsly-hosting/ssl'],
            )
            ssl_dir = os.path.join(temp_dir, "ssl")
            for cert_dir in cert_dirs:
                if os.path.isdir(cert_dir):
                    os.makedirs(ssl_dir, exist_ok=True)
                    try:
                        shutil.copytree(cert_dir, os.path.join(ssl_dir, os.path.basename(cert_dir)), dirs_exist_ok=True)
                    except Exception:
                        pass  # Non-critical

            # 2. Services Backup
            services_dir = os.path.join(temp_dir, "services")
            os.makedirs(services_dir, exist_ok=True)
            services = Service.objects.all()
            included = []

            for service in services:
                try:
                    sb = self.backup_service(service.id, db_only=backup.db_only)
                    included.append(str(sb.id))
                    # Move/Copy the service backup to our bundle
                    if sb.file_path and os.path.exists(sb.file_path):
                        shutil.copy2(sb.file_path, os.path.join(services_dir, os.path.basename(sb.file_path)))
                except Exception as e:
                    logger.error(f"Failed to backup service {service.name} during server backup: {e}")

            if services.exists() and not included:
                raise RuntimeError(
                    f"Server backup failed: {services.count()} service(s) exist but 0 were backed up. "
                    "Check the logs above for individual service backup errors."
                )

            # 2.5 Addons Backup
            addons_dir = os.path.join(temp_dir, "addons")
            os.makedirs(addons_dir, exist_ok=True)
            from apps.deployments.models_addons import Addon
            addons = Addon.objects.filter(status='ACTIVE')
            for addon in addons:
                # The user preference: "add addons that are persistent like s3, minio etc and leave other like redis, rabbitmq etc"
                if backup.db_only:
                    persistent_types = [
                        'POSTGRES', 'MYSQL', 'MARIADB', 'COCKROACHDB', 'TIMESCALEDB',
                        'PERCONA', 'VITESS', 'MONGODB', 'COUCHDB', 'RETHINKDB',
                        'ARANGODB', 'FERRETDB', 'SURREALDB', 'MINIO', 'SEAWEEDFS'
                    ]
                    if addon.addon_type not in persistent_types:
                        continue
                try:
                    dump_path = backup_addon(str(addon.id))
                    if dump_path and os.path.exists(dump_path):
                        shutil.copy2(dump_path, os.path.join(addons_dir, f"{addon.name}_{os.path.basename(dump_path)}"))
                except Exception as e:
                    logger.error(f"Failed to backup addon {addon.name} during server backup: {e}")

            # 3. Platform Config
            # SECURITY (Batch G): never include the Cloudflare API
            # token (or any future EncryptedCharField secret) in the
            # plaintext ``platform_config.json`` that ends up in the
            # tarball. The token is decrypted in memory by
            # ``EncryptedCharField.from_db_value`` and would otherwise
            # be stored in the clear inside the archive (and cleartext
            # on disk if the tar is not itself encrypted).
            from django.core import serializers

            from apps.deployments.models import PlatformConfig

            with open(os.path.join(temp_dir, "platform_config.json"), 'w') as f:
                # ``fields=[...]`` excludes EncryptedCharField secrets
                # from the serialized JSON. Only non-sensitive
                # configuration is included; to restore a Cloudflare
                # token, the operator must re-enter it on the target.
                data = serializers.serialize(
                    "json",
                    PlatformConfig.objects.all(),
                    fields=[
                        "id",
                        "domain",
                        "use_ssl",
                        "wildcard_subdomains",
                        "server_ip",
                        "caddy_status",
                        "max_concurrent_builds",
                        "updated_at",
                    ],
                )
                f.write(data)

            # 4. Final Tarball
            filename = f"server_backup_{uuid.uuid4().hex[:8]}.tar.gz"
            filepath = os.path.join(backups_dir, filename)

            with tarfile.open(filepath, "w:gz") as tar:
                tar.add(temp_dir, arcname="")

            filepath = self._maybe_encrypt(filepath)

            backup.services_included = included
            backup.file_path = filepath
            backup.status = 'COMPLETED'
            backup.size_bytes = os.path.getsize(filepath)
            backup.completed_at = timezone.now()
            backup.save()
            BackupService.stamp_encryption_header_into_metadata(backup.metadata, filepath)
            backup.save(update_fields=['metadata'])
            self._prune_old_backups(ServerBackup)

            # Upload to S3 if a cloud destination is configured.
            # Cloud failure is non-fatal — backup stays local and logs an alert.
            cloud_result = _upload_backup_to_cloud(backup, filepath, 'server')
            if not cloud_result["uploaded"] and cloud_result["reason"]:
                backup.metadata["cloud_upload_error"] = cloud_result["reason"]
                backup.save(update_fields=['metadata'])
                _alert_cloud_upload_failed(backup, cloud_result)

            return backup

        except Exception as e:
            logger.error("Server backup failed: %s\n%s", e, traceback.format_exc())
            backup.status = 'FAILED'
            backup.error_message = str(e)[:2000]
            backup.save()
            raise
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def restore_server(self, backup_id, requesting_user_id=None, raise_on_snapshot_failure=False):
        """
        Restore full server from backup.
        NOTE: This typically runs on a fresh server or replaces current state.
        """
        # Logic similar to restore_service but iterating over all services
        # and restoring the DB first.
        # This is complex because restoring DB while app is running is risky.
        # Usually requires a maintenance script.
        # For now, we'll implement the logic to unpack and trigger service restores.

        backup = ServerBackup.objects.get(id=backup_id)
        if backup.status != 'COMPLETED':
            raise ValueError("Only COMPLETED server backups can be restored.")
        archive_path, cleanup_archive = self._prepare_archive_for_restore(backup)
        temp_dir = os.path.join(os.path.dirname(archive_path), f"restore_srv_{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=True)

        # Resolve owner for single-service backup restores
        owner = None
        if requesting_user_id is not None:
            from django.contrib.auth import get_user_model
            try:
                owner = get_user_model().objects.get(id=requesting_user_id)
            except get_user_model().DoesNotExist:
                pass

        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_tar_extractall(tar, temp_dir)

            # Create pre-restore safety snapshots for each existing service
            # BEFORE restoring the database — captures the live state in case
            # the archive is corrupt or the wrong backup was selected.
            if requesting_user_id:
                from apps.deployments.models import Service
                for service in Service.objects.all():
                    try:
                        self.backup_service(service.id, backup_type='PRE_TRANSFER')
                        logger.info("Pre-restore snapshot created for service %s", service.name)
                    except Exception as e:
                        logger.warning("Pre-restore snapshot failed for service %s: %s", service.name, e)
                        if raise_on_snapshot_failure:
                            raise RuntimeError(
                                f"Pre-restore snapshot failed for {service.name}: {e}. "
                                "Refusing to restore."
                            ) from e

            # Restore the PostgreSQL database from the bundled dump.
            db_dump = os.path.join(temp_dir, "db_dump.sql")
            if os.path.exists(db_dump) and os.path.getsize(db_dump) > 0:
                self._restore_database_from_dump(db_dump)
            elif os.path.exists(db_dump):
                logger.warning("db_dump.sql is empty — skipping database restore.")
            else:
                logger.warning("No db_dump.sql found in server backup.")

            # Restore platform config if present.
            platform_config_path = os.path.join(temp_dir, "platform_config.json")
            if os.path.exists(platform_config_path):
                self._restore_platform_config(platform_config_path)

            # Create pre-restore safety snapshots for each existing service
            # (only on a running server — skip for fresh restore targets)
            if requesting_user_id:
                from apps.deployments.models import Service
                for service in Service.objects.all():
                    try:
                        self.backup_service(service.id, backup_type='PRE_TRANSFER')
                        logger.info("Pre-restore snapshot created for service %s", service.name)
                    except Exception as e:
                        logger.warning("Pre-restore snapshot failed for service %s: %s", service.name, e)
                        if raise_on_snapshot_failure:
                            raise RuntimeError(
                                f"Pre-restore snapshot failed for {service.name}: {e}. "
                                "Refusing to restore."
                            ) from e

            # Check if this is a full server backup (services/ dir) or a single service backup
            services_dir = os.path.join(temp_dir, "services")
            metadata_path = os.path.join(temp_dir, "metadata.json")
            restored = 0
            if os.path.exists(services_dir):
                for filename in os.listdir(services_dir):
                    if filename.endswith((".tar.gz", ".tar.gz.enc")):
                        self._restore_service_from_file(os.path.join(services_dir, filename), owner=owner)
                        restored += 1
            elif os.path.exists(metadata_path):
                logger.info("Detected single-service backup archive. Restoring directly.")
                self._restore_service_from_file(archive_path, owner=owner)
                restored += 1
            else:
                logger.warning("Server backup archive contains neither a services/ directory nor a metadata.json — nothing to restore.")

            if restored == 0 and (os.path.exists(services_dir) or os.path.exists(metadata_path)):
                files_found = os.listdir(services_dir) if os.path.exists(services_dir) else []
                raise RuntimeError(
                    f"Server restore completed but 0 services were restored. "
                    f"Archive contains {len(files_found)} file(s) in services/ "
                    f"(expected .tar.gz or .tar.gz.enc)."
                )

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            if cleanup_archive and os.path.exists(cleanup_archive):
                os.remove(cleanup_archive)

    def _restore_database_from_dump(self, dump_path):
        """Restore PostgreSQL database from a pg_dump file via the DB container."""
        logger.info("Restoring PostgreSQL database from %s ...", dump_path)
        try:
            pg_container = None
            for c in (self.docker_client.containers.list() if self.docker_client else []):
                c_image = (c.image.tags[0] if c.image.tags else '').lower()
                c_name = c.name.lower()
                if 'postgres' in c_image and 'pgcat' not in c_name:
                    pg_container = c
                    break
                if (('-db-' in c_name or c_name.endswith('-db')) and 'pgcat' not in c_name):
                    pg_container = c

            if not pg_container:
                raise RuntimeError("No PostgreSQL container found for database restore.")

            # Copy the dump file into the container and restore via docker-py.
            _copy_file_to_container(
                self.docker_client, pg_container.id,
                dump_path, '/tmp/db_dump.sql',
            )
            psql_res = pg_container.exec_run(
                ['psql', '-U', 'postgres',
                 '-v', 'ON_ERROR_STOP=1',
                 '--single-transaction',
                 '-f', '/tmp/db_dump.sql'],
                demux=False,
            )
            if psql_res.exit_code != 0:
                raise RuntimeError(
                    f"psql restore failed (exit {psql_res.exit_code}): "
                    f"{psql_res.output[:2000]!r}"
                )
            pg_container.exec_run(['rm', '/tmp/db_dump.sql'])
            logger.info("Database restored successfully from server backup.")
        except Exception as exc:
            logger.error("Database restore failed: %s. The backup archive is intact; operators can restore manually via psql.", exc)
            raise

    def _restore_platform_config(self, config_path):
        """Restore platform domain/SSL config from backup JSON."""
        try:
            import json as _json
            with open(config_path) as f:
                data = _json.load(f)
            from apps.deployments.models import PlatformConfig
            cfg = PlatformConfig.load()
            for field in ('domain', 'use_ssl', 'wildcard_subdomains', 'server_ip'):
                if data.get(field):
                    setattr(cfg, field, data[field])
            cfg.save()
            logger.info("Platform config restored from server backup.")
        except Exception as exc:
            logger.warning("Failed to restore platform_config.json: %s", exc)

    def _restore_service_from_file(self, filepath, owner=None):
        """Restore a service from a backup archive file.

        Args:
            filepath: Path to the service backup .tar.gz
            owner: User who owns the restored service (required for new services)
        """
        archive_path, cleanup_archive = self._prepare_archive_for_restore(filepath)
        temp_dir = os.path.join(os.path.dirname(archive_path), f"rest_tmp_{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=True)
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                _safe_tar_extractall(tar, temp_dir)

            with open(os.path.join(temp_dir, "metadata.json")) as f:
                metadata = json.load(f)

            # Create/Get Service
            service_name = metadata.get('service_name')
            service = Service.objects.filter(name=service_name).first()
            if not service:
                if not owner:
                    raise ValueError(
                        f"Cannot create service '{service_name}' without an owner. "
                        f"Pass owner to _restore_service_from_file.")
                service = Service.objects.create(
                    name=service_name,
                    owner=owner,
                    deploy_type=metadata.get('deploy_type', 'DOCKER'),
                    buildpack=metadata.get('buildpack', 'NIXPACKS'),
                    repository_url=metadata.get('git_url', ''),
                    branch=metadata.get('branch', 'main'),
                )

            # Create a temporary ServiceBackup record pointing to this file
            temp_backup = ServiceBackup.objects.create(
                service=service,
                file_path=filepath,
                status='COMPLETED'
            )
            self.restore_service(temp_backup.id, target_service_id=service.id)

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            if cleanup_archive and os.path.exists(cleanup_archive):
                os.remove(cleanup_archive)

    # ------------------------------------------------------------------
    # Progress broadcasting (WebSocket)
    # ------------------------------------------------------------------
    @staticmethod
    def _broadcast_progress(backup_id: str, stage: str, percent: float = 0,
                            message: str = '', bytes_transferred: int = 0,
                            total_bytes: int = 0) -> None:
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            from django.utils import timezone as tz
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"backup_progress_{backup_id}",
                    {
                        "type": "backup_progress",
                        "stage": stage,
                        "percent": percent,
                        "message": message,
                        "bytes_transferred": bytes_transferred,
                        "total_bytes": total_bytes,
                        "timestamp": tz.now().isoformat(),
                    },
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Hardening helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _crypto_chunk_size() -> int:
        try:
            value = int(os.environ.get("BACKUP_CRYPTO_CHUNK_SIZE", _DEFAULT_CRYPTO_CHUNK_SIZE))
        except (TypeError, ValueError):
            value = _DEFAULT_CRYPTO_CHUNK_SIZE
        return max(64 * 1024, value)

    @staticmethod
    def _decode_backup_key(key: str) -> bytes:
        try:
            raw = base64.urlsafe_b64decode(str(key).encode("ascii"))
        except (binascii.Error, UnicodeEncodeError) as exc:
            raise ValueError("BACKUP_ENCRYPTION_KEY must be a valid Fernet key") from exc
        if len(raw) != 32:
            raise ValueError("BACKUP_ENCRYPTION_KEY must decode to 32 bytes")
        return raw

    @staticmethod
    def _read_exact(file_obj, size: int) -> bytes:
        data = file_obj.read(size)
        if len(data) != size:
            raise ValueError("Encrypted backup is truncated")
        return data

    @staticmethod
    def get_encryption_header(filepath: str) -> dict | None:
        """Read the encryption header (key_id, fingerprint) from a backup file.

        Returns None if the file is not encrypted or header cannot be read.
        Safe to call on unencrypted files — simply returns None.
        """
        if not filepath or not filepath.endswith('.enc'):
            return None
        try:
            return BackupService.read_v2_header(filepath)
        except (ValueError, OSError):
            return None

    @staticmethod
    def stamp_encryption_header_into_metadata(metadata: dict, filepath: str) -> dict:
        """Augment backup metadata with the V2/V3 encryption key_id + fingerprint.

        Called after ``_maybe_encrypt`` so the backup JSON record carries
        the key identity alongside the tarball — downstream services and
        cross-master restores can look up the stored key without needing
        access to the encrypted file itself.
        """
        header = BackupService.get_encryption_header(filepath)
        if header:
            metadata['encryption'] = {
                'format': header.get('magic', ''),
                'key_id': header.get('key_id', ''),
                'fingerprint': header.get('fingerprint', ''),
            }
        return metadata

    @staticmethod
    def compute_backup_key_fingerprint(key_material: str) -> str:
        """Return 8-char hex fingerprint for a Fernet BACKUP_ENCRYPTION_KEY.

        Fingerprint is the first 4 bytes of SHA-256(raw 32-byte AES key).
        Stored in the V2 backup header so the target can verify the
        loaded key matches without attempting a decrypt.
        """
        try:
            raw_key = BackupService._decode_backup_key(key_material)
        except ValueError:
            raise
        return hashlib.sha256(raw_key).digest()[:_CHUNKED_BACKUP_FINGERPRINT_BYTES].hex()

    @staticmethod
    def resolve_or_register_active_key(key_material: str) -> dict:
        """Look up the BackupEncryptionKey row for ``key_material`` or create one.

        Returns ``{'key_id': <8 hex>, 'fingerprint': <8 hex>, 'created': bool}``.
        Called by the encrypt path so the V2 header can be stamped with
        a stable ``key_id``. Falls back to returning a synthetic
        ``key_id`` derived from the fingerprint when the DB is
        unavailable (e.g. inside a management command running with
        ``--no-database``); the V2 header is still written and the
        backup can still be decrypted locally via the env key.
        """
        fingerprint = BackupService.compute_backup_key_fingerprint(key_material)
        try:
            from apps.deployments.models_backup import BackupEncryptionKey
        except Exception:
            return {
                'key_id': fingerprint,
                'fingerprint': fingerprint,
                'created': False,
            }
        try:
            row = (
                BackupEncryptionKey.objects
                .filter(fingerprint=fingerprint, is_active=True)
                .first()
            )
            if row is not None:
                return {'key_id': row.key_id, 'fingerprint': fingerprint, 'created': False}
            existing_any = (
                BackupEncryptionKey.objects
                .filter(fingerprint=fingerprint)
                .first()
            )
            if existing_any is not None:
                existing_any.is_active = True
                existing_any.save(update_fields=['is_active'])
                return {
                    'key_id': existing_any.key_id,
                    'fingerprint': fingerprint,
                    'created': False,
                }
            key_id = os.urandom(_CHUNKED_BACKUP_KEY_ID_BYTES).hex()
            BackupEncryptionKey.objects.filter(is_active=True).update(is_active=False)
            BackupEncryptionKey.objects.create(
                key_id=key_id,
                fingerprint=fingerprint,
                key_material_encrypted=key_material,
                source='AUTO',
                is_active=True,
            )
            return {'key_id': key_id, 'fingerprint': fingerprint, 'created': True}
        except Exception as exc:
            logger.warning(
                "Could not register active backup key in DB (%s); "
                "falling back to fingerprint-derived key_id",
                exc,
            )
            return {
                'key_id': fingerprint,
                'fingerprint': fingerprint,
                'created': False,
            }

    @staticmethod
    def lookup_key_by_id(key_id: str) -> str | None:
        """Return the Fernet key material for a registered key_id or fingerprint, or None.

        Used by the decrypt path when the env key's fingerprint does
        not match the V2 header — i.e. the backup was encrypted on a
        different master and the operator has already imported the
        source key.
        """
        if not key_id:
            return None
        try:
            from django.db.models import Q
            from apps.deployments.models_backup import BackupEncryptionKey
            row = (
                BackupEncryptionKey.objects
                .filter(Q(key_id=key_id) | Q(fingerprint=key_id))
                .first()
            )
            if row is None:
                return None
            return row.key_material_encrypted
        except Exception:
            return None

    @staticmethod
    def read_v2_header(path: str) -> dict:
        """Read the V2 header (key_id, fingerprint) from an encrypted backup.

        Returns ``{'key_id': <8 hex>, 'fingerprint': <8 hex>, 'magic': 'V2'}``
        or raises :class:`ValueError` if the file is not V2 format.
        """
        with open(path, "rb") as source:
            magic = source.read(len(_CHUNKED_BACKUP_V3_MAGIC))
        if magic not in (_CHUNKED_BACKUP_V2_MAGIC, _CHUNKED_BACKUP_V3_MAGIC):
            raise ValueError("Backup is not V2 format (no key_id in header)")
        with open(path, "rb") as source:
            # Skip the format magic (same length for all magic strings)
            BackupService._read_exact(source, len(_CHUNKED_BACKUP_V3_MAGIC))
            key_id = BackupService._read_exact(
                source, _CHUNKED_BACKUP_KEY_ID_BYTES
            )
            fingerprint = BackupService._read_exact(
                source, _CHUNKED_BACKUP_FINGERPRINT_BYTES
            )
        magic_label = 'V3' if magic == _CHUNKED_BACKUP_V3_MAGIC else 'V2'
        return {
            'magic': magic_label,
            'key_id': key_id.hex(),
            'fingerprint': fingerprint.hex(),
        }

    @staticmethod
    def import_backup_key(
        key_id: str,
        key_material: str,
        label: str = '',
    ) -> dict:
        """Register a foreign key on this master for cross-master restore.

        ``key_id`` is the 8-char hex from the source backup's V2 header
        (operator copies it from the source's UI or the
        ``POST /backups/header/`` endpoint). ``key_material`` is the
        Fernet ``BACKUP_ENCRYPTION_KEY`` from the source's ``.env``.

        Idempotent: if a row with this key_id and the same fingerprint
        already exists, returns it. If a row with this key_id and a
        DIFFERENT fingerprint exists, raises :class:`BackupKeyCollisionError`
        (this would be a 1-in-2^32 random collision or an attempted
        key-swap attack; the operator should re-import from the source).
        """
        try:
            from apps.deployments.models_backup import BackupEncryptionKey
        except Exception as exc:
            raise RuntimeError(
                "BackupEncryptionKey model unavailable; cannot import key."
            ) from exc
        if not key_id or len(key_id) != _CHUNKED_BACKUP_KEY_ID_BYTES * 2:
            raise ValueError(
                f"key_id must be {_CHUNKED_BACKUP_KEY_ID_BYTES * 2} hex chars"
            )
        try:
            int(key_id, 16)
        except ValueError as exc:
            raise ValueError("key_id must be hex") from exc
        fingerprint = BackupService.compute_backup_key_fingerprint(key_material)
        existing = (
            BackupEncryptionKey.objects.filter(key_id=key_id).first()
        )
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise BackupKeyCollisionError(
                    f"key_id={key_id} already registered with a different "
                    f"fingerprint ({existing.fingerprint} != {fingerprint}). "
                    "Refusing to overwrite."
                )
            return {
                'key_id': existing.key_id,
                'fingerprint': existing.fingerprint,
                'source': existing.source,
                'created': False,
            }
        BackupEncryptionKey.objects.create(
            key_id=key_id,
            fingerprint=fingerprint,
            key_material_encrypted=key_material,
            source='IMPORTED',
            is_active=False,
            label=label or f'Imported on {timezone.now().isoformat()}',
        )
        return {
            'key_id': key_id,
            'fingerprint': fingerprint,
            'source': 'IMPORTED',
            'created': True,
        }

    def _maybe_encrypt(self, path: str) -> str:
        """
        Optionally encrypt backup archive at rest when BACKUP_ENCRYPTION_KEY is set.
        Uses chunked AES-GCM with the existing Fernet key material. Returns path
        to encrypted file and never loads the archive into memory.

        When ``settings.BACKUP_REQUIRE_ENCRYPTION`` is true (auto-enabled when
        ``DEBUG=False``), missing ``BACKUP_ENCRYPTION_KEY`` raises
        :class:`BackupEncryptionRequired` instead of silently writing the
        backup in cleartext.

        Writes a V2 header (``SMSLY-BACKUP-AESGCM-V2\n`` + 4-byte
        ``key_id`` + 4-byte ``fingerprint`` + 8-byte nonce prefix) so
        cross-master restores can look up the source key by ``key_id``.
        """
        key = BackupService._get_encryption_key()
        if not key:
            if self._backup_encryption_required():
                raise BackupEncryptionRequired(
                    "BACKUP_REQUIRE_ENCRYPTION is set but BACKUP_ENCRYPTION_KEY "
                    "is missing. Refusing to write an unencrypted backup."
                )
            return path

        enc_path = path + ".enc"
        try:
            raw_key = self._decode_backup_key(key)
            aesgcm = AESGCM(raw_key)
            nonce_prefix = os.urandom(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
            chunk_size = self._crypto_chunk_size()
            key_info = self.resolve_or_register_active_key(key)
            header_key_id = bytes.fromhex(key_info['key_id'][:_CHUNKED_BACKUP_KEY_ID_BYTES * 2])
            header_fingerprint = bytes.fromhex(
                key_info['fingerprint'][:_CHUNKED_BACKUP_FINGERPRINT_BYTES * 2]
            )

            with open(path, "rb") as source, open(enc_path, "wb") as encrypted:
                encrypted.write(_CHUNKED_BACKUP_V3_MAGIC)
                encrypted.write(header_key_id)
                encrypted.write(header_fingerprint)
                encrypted.write(nonce_prefix)
                chunk_index = 0
                while True:
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break
                    if chunk_index > 0xFFFFFFFF:
                        raise ValueError("Backup is too large for chunked encryption")
                    nonce = nonce_prefix + struct.pack(">I", chunk_index)
                    ciphertext = aesgcm.encrypt(nonce, chunk, None)
                    encrypted.write(struct.pack(">I", len(ciphertext)))
                    encrypted.write(ciphertext)
                    chunk_index += 1
                
                # Append encrypted EOF marker
                eof_nonce = nonce_prefix + struct.pack(">I", chunk_index)
                eof_ciphertext = aesgcm.encrypt(eof_nonce, b"EOF", None)
                encrypted.write(struct.pack(">I", len(eof_ciphertext)))
                encrypted.write(eof_ciphertext)
                
                encrypted.write(struct.pack(">I", 0))

            with contextlib.suppress(OSError):
                os.remove(path)
            return enc_path
        except Exception as e:
            # Clean up the partial encrypted file.
            try:
                if os.path.exists(enc_path):
                    os.remove(enc_path)
            except OSError:
                pass

            if self._backup_encryption_required():
                # Policy says cleartext is not allowed — delete the original
                # so we don't silently leave a plaintext backup on disk.
                with contextlib.suppress(OSError):
                    os.remove(path)
                raise BackupEncryptionRequired(
                    f"Encryption failed for {path}: {e}. "
                    "Refusing to retain cleartext backup when encryption "
                    "is mandatory."
                ) from e

            # Encryption is optional — the original backup is still useful
            # in cleartext. Log the failure and return the unencrypted path.
            logger.warning(
                "Encryption failed for %s: %s. "
                "BACKUP_REQUIRE_ENCRYPTION is not set, so the backup "
                "was stored in cleartext. Enable encryption or fix the "
                "error to protect backup data at rest.",
                path, e,
            )
            return path

    @staticmethod
    def _backup_encryption_required() -> bool:
        """
        Resolve the BACKUP_REQUIRE_ENCRYPTION flag. Prefers an explicit
        environment variable, then checks PlatformConfig (database/UI toggle),
        then falls back to ``settings.BACKUP_REQUIRE_ENCRYPTION``
        (which itself defaults to ``True`` when ``DEBUG=False``).
        """
        explicit = os.environ.get("BACKUP_REQUIRE_ENCRYPTION", "").strip().lower()
        if explicit in ("1", "true", "yes", "on"):
            return True
        if explicit in ("0", "false", "no", "off"):
            return False
        try:
            from apps.deployments.models_core import PlatformConfig
            config = PlatformConfig.load()
            if getattr(config, 'backup_require_encryption', None) is not None:
                return bool(config.backup_require_encryption)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        return bool(getattr(settings, "BACKUP_REQUIRE_ENCRYPTION", False))

    @staticmethod
    def decrypt_backup(path: str, key: str) -> str:
        """
        Decrypt an encrypted backup to a temp file in a private directory
        and return its path. Caller is responsible for deleting the temp file
        (use :func:`BackupService.cleanup_decrypted_path` to also remove the
        parent private directory).
        Supports the current V2 chunked AES-GCM format (with key_id +
        fingerprint header), the legacy V1 chunked format, and the
        pre-V1 Fernet archives without loading the encrypted or
        decrypted backup into process memory.

        For V2 backups where the passed ``key`` does not match the
        header's fingerprint (i.e. the backup was encrypted on a
        different master), this function will look up the key by
        ``key_id`` in the ``BackupEncryptionKey`` table. If not
        found, raises :class:`UnknownBackupKeyIdError` so the
        operator can call ``POST /backups/import-key/`` to import
        the source key.
        """
        with open(path, "rb") as source:
            magic = source.read(len(_CHUNKED_BACKUP_V3_MAGIC))
        if magic == _CHUNKED_BACKUP_V3_MAGIC:
            return BackupService._decrypt_v3_chunked_backup(path, key)
        elif magic.startswith(_CHUNKED_BACKUP_V2_MAGIC):
            return BackupService._decrypt_v2_chunked_backup(path, key)
        if magic.startswith(_CHUNKED_BACKUP_MAGIC):
            return BackupService._decrypt_chunked_backup(path, key)
        return BackupService._decrypt_legacy_fernet_backup(path, key)

    @staticmethod
    def can_decrypt_backup(path: str, passed_key: str = None) -> bool:
        """Return True if this master has an encryption key capable of decrypting path."""
        try:
            if not path or not os.path.exists(path) or not path.endswith('.enc'):
                return True
            header = BackupService.read_v2_header(path)
            if not header:
                return bool(BackupService._get_encryption_key() or passed_key)
            header_fingerprint = header.get('fingerprint', '')
            header_key_id = header.get('key_id', '')

            for key in (passed_key, BackupService._get_encryption_key()):
                if key:
                    try:
                        if BackupService.compute_backup_key_fingerprint(key) == header_fingerprint:
                            return True
                    except Exception:
                        pass

            if header_key_id and BackupService.lookup_key_by_id(header_key_id):
                return True
            if header_fingerprint and BackupService.lookup_key_by_id(header_fingerprint):
                return True
            try:
                from apps.deployments.models_backup import BackupEncryptionKey
                if BackupEncryptionKey.objects.filter(fingerprint=header_fingerprint).exists():
                    return True
            except Exception:
                pass
            return False
        except Exception:
            return bool(BackupService._get_encryption_key() or passed_key)

    @staticmethod
    def _resolve_key_for_v2(path: str, passed_key: str) -> tuple[bytes, str]:
        """Resolve the raw 32-byte AES key for a V2 backup.

        Strategy (in order):
        1. If the passed key's fingerprint matches the V2 header
           fingerprint, use it (covers same-master + same-key case).
        2. Otherwise, look up the key by key_id in
           ``BackupEncryptionKey``. If the imported key's fingerprint
           matches, use it (covers cross-master import case).
        3. Otherwise, raise :class:`UnknownBackupKeyIdError` so the
           operator can import the source key.

        Returns ``(raw_key_bytes, fingerprint_hex)``.
        """
        with open(path, "rb") as source:
            # Skip the format magic (all magics have the same length)
            BackupService._read_exact(source, len(_CHUNKED_BACKUP_V3_MAGIC))
            header_key_id = BackupService._read_exact(
                source, _CHUNKED_BACKUP_KEY_ID_BYTES
            )
            header_fingerprint = BackupService._read_exact(
                source, _CHUNKED_BACKUP_FINGERPRINT_BYTES
            )
        header_fingerprint_hex = header_fingerprint.hex()
        header_key_id_hex = header_key_id.hex()

        if passed_key:
            try:
                passed_fingerprint = BackupService.compute_backup_key_fingerprint(passed_key)
                if passed_fingerprint == header_fingerprint_hex:
                    return BackupService._decode_backup_key(passed_key), header_fingerprint_hex
            except Exception:
                pass

        active_key = BackupService._get_encryption_key()
        if active_key and active_key != passed_key:
            try:
                active_fingerprint = BackupService.compute_backup_key_fingerprint(active_key)
                if active_fingerprint == header_fingerprint_hex:
                    return BackupService._decode_backup_key(active_key), header_fingerprint_hex
            except Exception:
                pass

        for search_id in (header_key_id_hex, header_fingerprint_hex):
            if not search_id:
                continue
            imported_key_material = BackupService.lookup_key_by_id(search_id)
            if imported_key_material is not None:
                try:
                    imported_fingerprint = BackupService.compute_backup_key_fingerprint(
                        imported_key_material
                    )
                    if imported_fingerprint == header_fingerprint_hex:
                        return (
                            BackupService._decode_backup_key(imported_key_material),
                            header_fingerprint_hex,
                        )
                except Exception:
                    pass

        try:
            from apps.deployments.models_backup import BackupEncryptionKey
            row = BackupEncryptionKey.objects.filter(fingerprint=header_fingerprint_hex).first()
            if row and row.key_material_encrypted:
                return (
                    BackupService._decode_backup_key(row.key_material_encrypted),
                    header_fingerprint_hex,
                )
        except Exception:
            pass

        raise UnknownBackupKeyIdError(
            key_id=header_key_id_hex,
            fingerprint=header_fingerprint_hex,
            message=(
                f"Backup was encrypted with key_id={header_key_id_hex} "
                f"(fingerprint={header_fingerprint_hex}) which is not "
                "registered on this master. Call POST /backups/import-key/ "
                "to import the source key, then retry."
            ),
        )

    @staticmethod
    def _decrypt_v2_chunked_backup(path: str, key: str) -> str:
        raw_key, _fingerprint = BackupService._resolve_key_for_v2(path, key)
        aesgcm = AESGCM(raw_key)

        tmp_path, _private_dir = BackupService._make_private_decrypted_path()
        try:
            with open(path, "rb") as source, open(tmp_path, "wb") as target:
                magic = BackupService._read_exact(source, len(_CHUNKED_BACKUP_V2_MAGIC))
                if magic != _CHUNKED_BACKUP_V2_MAGIC:
                    raise ValueError("Unsupported encrypted backup format (V2 expected)")
                source.read(_CHUNKED_BACKUP_KEY_ID_BYTES + _CHUNKED_BACKUP_FINGERPRINT_BYTES)
                nonce_prefix = BackupService._read_exact(
                    source, _CHUNKED_BACKUP_NONCE_PREFIX_BYTES
                )
                chunk_index = 0
                while True:
                    length_raw = BackupService._read_exact(source, 4)
                    chunk_length = struct.unpack(">I", length_raw)[0]
                    if chunk_length == 0:
                        break
                    ciphertext = BackupService._read_exact(source, chunk_length)
                    nonce = nonce_prefix + struct.pack(">I", chunk_index)
                    target.write(aesgcm.decrypt(nonce, ciphertext, None))
                    chunk_index += 1
            return tmp_path
        except Exception:
            BackupService.cleanup_decrypted_path(tmp_path)
            raise

    @staticmethod
    def _decrypt_v3_chunked_backup(path: str, key: str) -> str:
        raw_key, _fingerprint = BackupService._resolve_key_for_v2(path, key)
        aesgcm = AESGCM(raw_key)

        tmp_path, _private_dir = BackupService._make_private_decrypted_path()
        try:
            with open(path, "rb") as source, open(tmp_path, "wb") as target:
                magic = BackupService._read_exact(source, len(_CHUNKED_BACKUP_V3_MAGIC))
                if magic != _CHUNKED_BACKUP_V3_MAGIC:
                    raise ValueError("Unsupported encrypted backup format (V3 expected)")
                source.read(_CHUNKED_BACKUP_KEY_ID_BYTES + _CHUNKED_BACKUP_FINGERPRINT_BYTES)
                nonce_prefix = BackupService._read_exact(
                    source, _CHUNKED_BACKUP_NONCE_PREFIX_BYTES
                )
                chunk_index = 0
                last_plaintext = None
                while True:
                    length_raw = BackupService._read_exact(source, 4)
                    chunk_length = struct.unpack(">I", length_raw)[0]
                    if chunk_length == 0:
                        break
                    ciphertext = BackupService._read_exact(source, chunk_length)
                    nonce = nonce_prefix + struct.pack(">I", chunk_index)
                    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                    if last_plaintext is not None:
                        target.write(last_plaintext)
                    last_plaintext = plaintext
                    chunk_index += 1
                if last_plaintext != b"EOF":
                    raise ValueError("Backup decryption failed: Missing or invalid EOF marker (possible truncation attack)")
            return tmp_path
        except Exception:
            BackupService.cleanup_decrypted_path(tmp_path)
            raise


    @staticmethod
    def _make_private_decrypted_path(suffix: str = ".tar.gz") -> tuple:
        """Create a private directory under /tmp for a decrypted backup.

        The directory is created with mode 0o700 and the file with mode 0o600
        to avoid leaking plaintext backups through permissive umasks.
        Returns ``(tmp_path, private_dir)``.
        """
        import uuid as _uuid
        private_dir = os.path.join(
            tempfile.gettempdir(),
            f"smsly-decrypted-{_uuid.uuid4().hex}",
        )
        os.makedirs(private_dir, mode=0o700, exist_ok=False)
        with contextlib.suppress(OSError):
            os.chmod(private_dir, 0o700)
        fd, tmp_path = tempfile.mkstemp(
            prefix="backup_dec_", suffix=suffix, dir=private_dir,
        )
        os.close(fd)
        with contextlib.suppress(OSError):
            os.chmod(tmp_path, 0o600)
        return tmp_path, private_dir

    @staticmethod
    def cleanup_decrypted_path(path: str) -> None:
        """Remove a decrypted backup file and its private parent directory.

        Safe to call on paths that do not exist. Only removes the parent
        directory if it matches the expected ``/tmp/smsly-decrypted-*`` pattern.
        """
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        parent = os.path.dirname(os.path.abspath(path))
        if not parent:
            return
        if not os.path.basename(parent).startswith('smsly-decrypted-'):
            return
        try:
            for entry in os.listdir(parent):
                if entry.startswith('backup_dec_'):
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(parent, entry))
        except OSError:
            pass
        with contextlib.suppress(OSError):
            os.rmdir(parent)

    @staticmethod
    def _decrypt_chunked_backup(path: str, key: str) -> str:
        raw_key = BackupService._decode_backup_key(key)
        aesgcm = AESGCM(raw_key)

        tmp_path, _private_dir = BackupService._make_private_decrypted_path()
        try:
            with open(path, "rb") as source, open(tmp_path, "wb") as target:
                magic = BackupService._read_exact(source, len(_CHUNKED_BACKUP_MAGIC))
                if magic != _CHUNKED_BACKUP_MAGIC:
                    raise ValueError("Unsupported encrypted backup format")
                nonce_prefix = BackupService._read_exact(
                    source, _CHUNKED_BACKUP_NONCE_PREFIX_BYTES
                )
                chunk_index = 0
                while True:
                    length_raw = BackupService._read_exact(source, 4)
                    chunk_length = struct.unpack(">I", length_raw)[0]
                    if chunk_length == 0:
                        break
                    ciphertext = BackupService._read_exact(source, chunk_length)
                    nonce = nonce_prefix + struct.pack(">I", chunk_index)
                    target.write(aesgcm.decrypt(nonce, ciphertext, None))
                    chunk_index += 1
            return tmp_path
        except Exception:
            BackupService.cleanup_decrypted_path(tmp_path)
            raise

    @staticmethod
    def _decode_fernet_token_to_file(path: str) -> str:
        fd, token_path = tempfile.mkstemp(prefix="backup_token_", suffix=".bin")
        os.close(fd)
        remainder = b""
        try:
            with open(path, "rb") as source, open(token_path, "wb") as target:
                while True:
                    encoded = source.read(1024 * 1024)
                    if not encoded:
                        break
                    encoded = remainder + b"".join(encoded.split())
                    usable = (len(encoded) // 4) * 4
                    if usable:
                        target.write(base64.urlsafe_b64decode(encoded[:usable]))
                    remainder = encoded[usable:]
                if remainder:
                    padding_len = (-len(remainder)) % 4
                    target.write(base64.urlsafe_b64decode(remainder + (b"=" * padding_len)))
            return token_path
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(token_path)
            raise

    @staticmethod
    def _decrypt_legacy_fernet_backup(path: str, key: str) -> str:
        # Validate key using Fernet too, so legacy key errors retain the same behavior.
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ValueError("BACKUP_ENCRYPTION_KEY must be a valid Fernet key") from exc

        raw_key = BackupService._decode_backup_key(key)
        signing_key = raw_key[:16]
        encryption_key = raw_key[16:]

        token_path = BackupService._decode_fernet_token_to_file(path)
        tmp_path, _private_dir = BackupService._make_private_decrypted_path()
        try:
            token_size = os.path.getsize(token_path)
            min_size = _FERNET_HEADER_SIZE + _FERNET_HMAC_SIZE + 16
            if token_size < min_size:
                raise ValueError("Failed to decrypt backup archive: invalid token")

            signed_size = token_size - _FERNET_HMAC_SIZE
            verifier = hmac.HMAC(signing_key, hashes.SHA256())
            with open(token_path, "rb") as token_file:
                remaining = signed_size
                while remaining:
                    chunk = token_file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Encrypted backup is truncated")
                    verifier.update(chunk)
                    remaining -= len(chunk)
                expected_signature = BackupService._read_exact(token_file, _FERNET_HMAC_SIZE)
            try:
                verifier.verify(expected_signature)
            except InvalidSignature as exc:
                raise ValueError("Failed to decrypt backup archive: invalid token") from exc

            with open(token_path, "rb") as token_file:
                version = BackupService._read_exact(token_file, 1)
                if version != b"\x80":
                    raise ValueError("Failed to decrypt backup archive: invalid token")
                BackupService._read_exact(token_file, 8)  # timestamp
                iv = BackupService._read_exact(token_file, 16)
                ciphertext_size = signed_size - _FERNET_HEADER_SIZE
                decryptor = Cipher(
                    algorithms.AES(encryption_key),
                    modes.CBC(iv),
                ).decryptor()
                unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()

                with open(tmp_path, "wb") as target:
                    remaining = ciphertext_size
                    while remaining:
                        chunk = token_file.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Encrypted backup is truncated")
                        remaining -= len(chunk)
                        plaintext = decryptor.update(chunk)
                        if plaintext:
                            target.write(unpadder.update(plaintext))
                    plaintext = decryptor.finalize()
                    if plaintext:
                        target.write(unpadder.update(plaintext))
                    target.write(unpadder.finalize())

            return tmp_path
        except InvalidToken as e:
            BackupService.cleanup_decrypted_path(tmp_path)
            raise ValueError("Failed to decrypt backup archive: invalid token") from e
        except Exception:
            BackupService.cleanup_decrypted_path(tmp_path)
            raise
        finally:
            with contextlib.suppress(OSError):
                os.remove(token_path)

    @staticmethod
    def _prune_old_backups(model_cls, service_id=None):
        """Delete old backup records and their files.

        The retention count is controlled by the ``BACKUP_RETENTION_COUNT``
        environment variable (default ``5``).  For ``ServiceBackup`` the pruning
        is scoped to a single service; for ``ServerBackup`` it is global.
        Both the database rows *and* the associated backup files on disk are
        removed.

        Uses a cutoff timestamp to avoid races: the Nth newest backup's
        ``created_at`` is used as the boundary so that concurrent backups
        that complete between the query and the delete are correctly kept
        or pruned based on their creation time rather than a fixed ID set.
        """
        try:
            retain = int(os.environ.get("BACKUP_RETENTION_COUNT", "5"))
        except ValueError:
            retain = 5
        retain = max(retain, 1)

        qs = model_cls.objects.order_by("-created_at")
        if service_id and hasattr(model_cls, "service_id"):
            qs = qs.filter(service_id=service_id)

        # Get the Nth newest backup's creation date as the cutoff.
        # We use the retain-th newest's created_at so that anything older
        # (created_at strictly less) is pruned.  This avoids a race where
        # a backup that completes between the cutoff query and the delete
        # is accidentally removed — its created_at will be >= the cutoff.
        nth_newest = qs.values_list("created_at", flat=True)[retain - 1:retain].first()
        if nth_newest is None:
            return  # Fewer than retain backups exist — nothing to prune.

        # Use a datetime-based cutoff instead of a fixed set of IDs.
        old_backups = model_cls.objects.filter(created_at__lt=nth_newest)
        if service_id and hasattr(model_cls, "service_id"):
            old_backups = old_backups.filter(service_id=service_id)

        # Delete files first so we don't lose the path after the DB row is gone
        for backup in old_backups.iterator():
            try:
                if backup.file_path and os.path.exists(backup.file_path):
                    os.remove(backup.file_path)
            except Exception as exc:  # pragma: no cover – defensive
                logger.warning(
                    "Failed to delete backup file %s for %s %s: %s",
                    backup.file_path,
                    model_cls.__name__,
                    backup.id,
                    exc,
                )

            # Also delete the cloud object if the backup was uploaded.
            if getattr(backup, 'cloud_uploaded', False):
                try:
                    _delete_backup_cloud_object(backup)
                except Exception as exc:  # pragma: no cover – defensive
                    logger.warning(
                        "Failed to delete cloud object for backup %s: %s",
                        backup.id, exc,
                    )

        # Finally delete the DB rows
        old_backups.delete()


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

    from apps.deployments.models_core import EnvironmentVariable
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


def _dump_container_database(container_name, image_tag, temp_dir):
    """Run pg_dump/mysqldump/redis SAVE inside a DB container for consistent backups."""
    import docker as _docker
    client = _docker.from_env()
    try:
        ctr = client.containers.get(container_name)
        image_lower = (image_tag or '').lower()
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
            client.exec_command(f"docker stop {container_name} 2>/dev/null || true", raise_on_error=False)
            client.exec_command(
                f"for i in $(seq 1 15); do "
                f"  docker inspect -f '{{{{.State.Status}}}}' {container_name} 2>/dev/null | grep -q exited && break; "
                f"  sleep 1; "
                f"done",
                raise_on_error=False,
            )
            client.close()
        else:
            import docker as _docker
            client = _docker.from_env()
            try:
                ctr = client.containers.get(container_name)
                ctr.stop(timeout=30)
                ctr.wait(condition='not-running', timeout=30)
            except Exception:
                pass
        logger.info("Stopped service %s before restore", service.name)
    except Exception as exc:
        logger.warning("Could not stop service %s before restore: %s", service.name, exc)


def _emergency_restart_container(service):
    """Last-resort restart of a stopped container after restore.

    Called when provider resolution fails after volumes/db have been
    restored — without this the service would silently stay dead.
    """
    container_name = service.name
    try:
        import docker as _docker
        client = _docker.from_env()
        ctr = client.containers.get(container_name)
        ctr.start()
        logger.info("Emergency restart: started container %s", container_name)
    except docker.errors.NotFound:
        logger.warning("Emergency restart: container %s not found — service will stay stopped", container_name)
    except Exception as exc:
        logger.warning("Emergency restart failed for %s: %s", container_name, exc)


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
        ssh.exec_command(f"docker start {container_name} 2>/dev/null || true", raise_on_error=False)
        ssh.close()
        logger.info("Emergency remote restart: started container %s on %s", container_name, server.host)
    except Exception as exc:
        logger.warning("Emergency remote restart failed for %s on %s: %s", container_name, getattr(server, 'host', '?'), exc)


def backup_addon(addon_id: str) -> str | None:
    """Back up a single addon (Postgres/MySQL/Redis/Mongo). Returns path to dump file or None."""
    import docker as _docker

    from apps.deployments.models_addons import Addon
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

        # Do not remap if the target domain is an IP address
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
    except Exception:
        pass


def normalize_s3_key(s3_key, bucket=None):
    """Normalize an S3 key copied from various cloud dashboard formats.

    Handles: s3://bucket/key, https://host/bucket/key, bucket/key, bucket/bucket/key
    """
    from urllib.parse import urlparse

    key = s3_key.strip()

    if key.startswith('s3://'):
        key = key[5:]

    if key.startswith(('http://', 'https://')):
        parsed = urlparse(key)
        key = parsed.path.lstrip('/')

    key = key.lstrip('/')

    if bucket:
        while key.startswith(bucket + '/') or key == bucket:
            if key == bucket:
                key = ''
            else:
                key = key[len(bucket) + 1:]

    return key


def list_s3_objects(
    bucket: str,
    prefix: str = '',
    endpoint: str = '',
    region: str = 'us-east-1',
    access_key: str = '',
    secret_key: str = '',
    max_keys: int = 200,
) -> list[dict]:
    """List objects in an S3 bucket with the given prefix.

    Returns a list of dicts with 'key', 'size', 'last_modified'.
    Returns empty list on any error (connection, auth, etc).
    """
    try:
        client = _get_s3_client(endpoint, region, access_key, secret_key)
        kwargs = {'Bucket': bucket, 'MaxKeys': max_keys}
        if prefix:
            kwargs['Prefix'] = prefix
        response = client.list_objects_v2(**kwargs)
        contents = response.get('Contents', [])
        return [
            {
                'key': obj['Key'],
                'size': obj['Size'],
                'last_modified': obj['LastModified'].isoformat(),
            }
            for obj in contents
        ]
    except Exception as exc:
        logger.warning("Failed to list S3 objects in %s/%s: %s", bucket, prefix, exc)
        return []


def _get_s3_client(endpoint='', region='us-east-1',
                   access_key='', secret_key=''):
    """Build a boto3 S3 client with the given credentials."""
    import boto3
    from botocore.client import Config
    kwargs = {'aws_access_key_id': access_key,
              'aws_secret_access_key': secret_key,
              'region_name': region,
              'config': Config(signature_version='s3v4')}
    if endpoint:
        if not endpoint.startswith(('http://', 'https://')):
            endpoint = 'https://' + endpoint
        kwargs['endpoint_url'] = endpoint
    return boto3.client('s3', **kwargs)


def _s3_upload_with_retry(client, local_path, s3_bucket, s3_key,
                          max_retries=3, progress_callback=None) -> bool:
    """Upload a file to S3 with exponential backoff retry."""
    import time
    from botocore.s3.transfer import TransferConfig
    config = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        max_concurrency=4,
        use_threads=True,
    )
    for attempt in range(1, max_retries + 1):
        try:
            extra_args = {}
            if progress_callback:
                extra_args['Callback'] = progress_callback
            client.upload_file(
                local_path, s3_bucket, s3_key,
                Config=config,
                **extra_args,
            )
            return True
        except Exception as exc:
            logger.warning(
                "S3 upload attempt %d/%d failed for %s/%s: %s",
                attempt, max_retries, s3_bucket, s3_key, exc,
            )
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return False


def _s3_delete_with_retry(client, s3_bucket, s3_key, max_retries=3) -> bool:
    """Delete an S3 object with exponential backoff retry."""
    import time
    for attempt in range(1, max_retries + 1):
        try:
            client.delete_object(Bucket=s3_bucket, Key=s3_key)
            return True
        except Exception as exc:
            logger.warning(
                "S3 delete attempt %d/%d failed for %s/%s: %s",
                attempt, max_retries, s3_bucket, s3_key, exc,
            )
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return False


def _s3_download_with_retry(client, s3_bucket, s3_key, local_path,
                            max_retries=3, progress_callback=None) -> bool:
    """Download a file from S3 with exponential backoff retry."""
    import time
    for attempt in range(1, max_retries + 1):
        try:
            extra_args = {}
            if progress_callback:
                extra_args['Callback'] = progress_callback
            client.download_file(
                s3_bucket, s3_key, local_path,
                **extra_args,
            )
            return True
        except Exception as exc:
            logger.warning(
                "S3 download attempt %d/%d failed for %s/%s: %s",
                attempt, max_retries, s3_bucket, s3_key, exc,
            )
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return False


def upload_backup_to_s3(local_path: str, s3_bucket: str, s3_key: str,
                        endpoint: str = '', region: str = 'us-east-1',
                        access_key: str = '', secret_key: str = '',
                        progress_callback=None) -> bool:
    """Upload a backup file to S3 (or R2/MinIO via custom endpoint) with retry."""
    try:
        client = _get_s3_client(endpoint, region, access_key, secret_key)
        ok = _s3_upload_with_retry(client, local_path, s3_bucket, s3_key, progress_callback=progress_callback)
        if ok:
            logger.info("Backup uploaded to s3://%s/%s", s3_bucket, s3_key)
        else:
            logger.error("S3 upload failed after retries for s3://%s/%s", s3_bucket, s3_key)
        return ok
    except ImportError:
        logger.warning("boto3 not available — S3 upload skipped")
    except Exception as exc:
        logger.error("S3 upload failed: %s", exc)
    return False


def download_from_s3(s3_bucket: str, s3_key: str, local_path: str,
                     endpoint: str = '', region: str = 'us-east-1',
                     access_key: str = '', secret_key: str = '',
                     progress_callback=None) -> bool:
    """Download a backup file from S3 (or R2/MinIO) to local path with retry."""
    try:
        client = _get_s3_client(endpoint, region, access_key, secret_key)
        ok = _s3_download_with_retry(client, s3_bucket, s3_key, local_path, progress_callback=progress_callback)
        if ok:
            logger.info("Backup downloaded from s3://%s/%s to %s", s3_bucket, s3_key, local_path)
        else:
            logger.error("S3 download failed after retries for s3://%s/%s", s3_bucket, s3_key)
        return ok
    except ImportError:
        logger.warning("boto3 not available — S3 download skipped")
    except Exception as exc:
        logger.error("S3 download failed: %s", exc)
    return False


def delete_cloud_backup_object(s3_bucket: str, s3_key: str,
                               endpoint: str = '', region: str = 'us-east-1',
                               access_key: str = '', secret_key: str = '') -> bool:
    """Delete a previously-uploaded backup object from S3 (or R2/MinIO) with retry."""
    try:
        client = _get_s3_client(endpoint, region, access_key, secret_key)
        ok = _s3_delete_with_retry(client, s3_bucket, s3_key)
        if ok:
            logger.info("Deleted s3://%s/%s", s3_bucket, s3_key)
        else:
            logger.error("S3 delete failed after retries for %s/%s", s3_bucket, s3_key)
        return ok
    except ImportError:
        logger.warning("boto3 not available — S3 delete skipped")
    except Exception as exc:
        logger.error("S3 delete failed for %s/%s: %s", s3_bucket, s3_key, exc)
    return False


def _upload_backup_to_cloud(backup, filepath, service_name):
    """Upload a backup to cloud storage and track metadata on the backup record.
    
    Accepts either a ServiceBackup or ServerBackup instance. Updates cloud_*
    fields on success. Returns a dict::

        {"uploaded": True/False, "reason": "...", "bucket": "...", "key": "..."}

    Respects the ``cloud_upload_enabled`` flag on the matching BackupSchedule —
    if the schedule exists and has it set to False, cloud upload is skipped
    even when credentials are configured.
    """
    result: dict[str, Any] = {"uploaded": False, "reason": "", "bucket": "", "key": ""}
    try:
        from apps.deployments.models_backup import BackupSchedule
        service_id = getattr(backup, 'service_id', None)
        dest = getattr(backup, 'cloud_destination', None)

        # Check schedule for cloud_upload_enabled toggle
        if service_id:
            sched = BackupSchedule.objects.filter(
                service_id=service_id, enabled=True,
            ).first()
        else:
            sched = BackupSchedule.objects.filter(
                is_server_wide=True, enabled=True,
            ).first()

        if sched is not None and not sched.cloud_upload_enabled:
            result["reason"] = "cloud_upload_enabled=False on schedule"
            logger.info(
                "Cloud upload skipped for %s: %s",
                service_name, result["reason"],
            )
            return result
        
        if dest:
            s3_bucket = dest.bucket
            s3_endpoint = dest.endpoint_url
            s3_region = dest.region
            s3_access_key = dest.access_key
            s3_secret_key = dest.secret_key
        else:
            if not sched or sched.storage_backend != 's3' or not sched.s3_bucket or not sched.s3_access_key:
                result["reason"] = "No S3 destination configured"
                return result
            s3_bucket = sched.s3_bucket
            s3_endpoint = sched.s3_endpoint
            s3_region = sched.s3_region
            s3_access_key = sched.s3_access_key
            s3_secret_key = sched.s3_secret_key

        # Enforce maximum upload size before attempting S3 transfer.
        try:
            max_bytes = int(os.environ.get("BACKUP_MAX_SIZE_BYTES", str(_DEFAULT_MAX_BACKUP_SIZE)))
        except (TypeError, ValueError):
            max_bytes = _DEFAULT_MAX_BACKUP_SIZE
        file_size = os.path.getsize(filepath)
        if max_bytes > 0 and file_size > max_bytes:
            result["reason"] = f"Backup size ({file_size} bytes) exceeds BACKUP_MAX_SIZE_BYTES ({max_bytes} bytes)"
            logger.warning("Skipping S3 upload for %s: %s", service_name, result["reason"])
            return result

        result["bucket"] = s3_bucket
        result["key"] = f"smsly-backups/{service_name}/{os.path.basename(filepath)}"
        backup_id_str = str(getattr(backup, 'id', ''))
        class _S3UploadProgress:
            def __init__(self):
                self.total = os.path.getsize(filepath)
                self.transferred = 0
            def __call__(self, bytes_amount):
                self.transferred += bytes_amount
                pct = min(95, (self.transferred / max(self.total, 1)) * 100)
                BackupService._broadcast_progress(
                    backup_id_str, 'cloud_upload', percent=pct,
                    message=f'Uploading... {self.transferred // (1024 * 1024)} MB',
                    bytes_transferred=self.transferred, total_bytes=self.total,
                )
        progress = _S3UploadProgress()
        ok = upload_backup_to_s3(
            filepath, s3_bucket, result["key"],
            endpoint=s3_endpoint, region=s3_region,
            access_key=s3_access_key, secret_key=s3_secret_key,
            progress_callback=progress,
        )
        if ok:
            backup.cloud_uploaded = True
            backup.cloud_bucket = s3_bucket
            backup.cloud_key = result["key"]
            backup.save(update_fields=['cloud_uploaded', 'cloud_bucket', 'cloud_key'])
            result["uploaded"] = True
        else:
            result["reason"] = "S3 upload returned failure — check credentials, network, or bucket permissions"
        return result
    except Exception as exc:
        result["reason"] = str(exc)
        logger.warning("Cloud upload skipped for %s: %s", service_name, exc)
    return result


def _alert_cloud_upload_failed(backup, cloud_result: dict):
    """Log audit event and create in-app notification when cloud upload fails.

    Cloud failure is non-fatal — the backup was saved locally — but the
    operator needs to know so they can investigate and retry.
    """
    try:
        from django.utils import timezone as tz

        backup_type = "server" if getattr(backup, 'services_included', None) is not None else "service"
        service_id = getattr(backup, 'service_id', None)
        backup_id = str(getattr(backup, 'id', ''))
        bucket = cloud_result.get('bucket', '')
        key = cloud_result.get('key', '')
        reason = cloud_result.get('reason', 'unknown')

        from apps.deployments.utils import log_event
        log_event(
            action='BACKUP_CLOUD_UPLOAD_FAILED',
            target=f'{backup_type.capitalize()} backup {backup_id}',
            actor='system',
            metadata={
                'backup_id': backup_id,
                'backup_type': backup_type,
                'service_id': str(service_id) if service_id else None,
                'bucket': bucket,
                'key': key,
                'reason': reason,
                'timestamp': tz.now().isoformat(),
            },
        )

        # Create in-app notification if the backup belongs to a service
        from apps.deployments.models import Service
        if service_id:
            try:
                svc = Service.objects.select_related('owner').only(
                    'name', 'owner',
                ).get(id=service_id)
            except Service.DoesNotExist:
                svc = None
            if svc and svc.owner:
                try:
                    from apps.notifications.models import Notification
                    Notification.objects.create(
                        user=svc.owner,
                        title='Cloud backup upload failed',
                        message=(
                            f"Backup of '{svc.name}' completed locally but "
                            f"could not be uploaded to cloud storage "
                            f"({cloud_result.get('bucket', 'S3')}). "
                            f"Reason: {reason}. The backup is safe on the "
                            f"local server."
                        ),
                        event_type='backup_cloud_failed',
                    )
                except Exception:
                    pass

            # Also dispatch alert to configured channels via Celery
            try:
                from apps.deployments.tasks_alerts import (
                    _send_alerts_for_backup_cloud_failure,
                )
                _send_alerts_for_backup_cloud_failure.delay(
                    service_id=str(service_id),
                    backup_id=backup_id,
                    reason=str(reason),
                    bucket=str(bucket),
                    key=str(key),
                )
            except Exception:
                pass

    except Exception as exc:
        logger.warning("Failed to create cloud upload alert: %s", exc)


def _resolve_cloud_config(backup):
    """Resolve cloud bucket + key + credentials for a backup record.
    
    Priority: 1) stored cloud fields on backup, 2) BackupSchedule lookup.
    Returns (bucket, key, endpoint, region, access_key, secret_key) or
    (None, None, None, None, None, None) if nothing found.
    """
    bucket = getattr(backup, 'cloud_bucket', '') or ''
    key = getattr(backup, 'cloud_key', '') or ''
    if bucket and key:
        dest = getattr(backup, 'cloud_destination', None)
        if dest:
            return bucket, key, dest.endpoint, dest.region, dest.access_key, dest.secret_key
        return bucket, key, '', 'us-east-1', '', ''
    from apps.deployments.models_backup import BackupSchedule
    service_id = getattr(backup, 'service_id', None)
    if service_id:
        sched = BackupSchedule.objects.filter(
            service_id=service_id, enabled=True, storage_backend='s3',
        ).first()
        if sched and sched.s3_bucket and sched.s3_access_key:
            service_name = getattr(getattr(backup, 'service', None), 'name', 'unknown')
            derived_key = f"smsly-backups/{service_name}/{os.path.basename(backup.file_path or 'unknown')}"
            return (sched.s3_bucket, derived_key, sched.s3_endpoint,
                    sched.s3_region, sched.s3_access_key, sched.s3_secret_key)
    else:
        sched = BackupSchedule.objects.filter(
            is_server_wide=True, enabled=True, storage_backend='s3',
        ).first()
        if sched and sched.s3_bucket and sched.s3_access_key:
            derived_key = f"smsly-backups/server/{os.path.basename(backup.file_path or 'unknown')}"
            return (sched.s3_bucket, derived_key, sched.s3_endpoint,
                    sched.s3_region, sched.s3_access_key, sched.s3_secret_key)
    return None, None, None, None, None, None


def _download_backup_from_cloud(backup, local_path) -> bool:
    """Download a backup from cloud storage to local path.
    
    Uses stored cloud_* fields on the backup record first, then falls
    back to BackupSchedule lookup. Returns True on success.
    """
    bucket, key, endpoint, region, access_key, secret_key = _resolve_cloud_config(backup)
    if not bucket or not key:
        logger.warning("No cloud config found to download backup %s", backup.id)
        return False
    backup_id_str = str(getattr(backup, 'id', ''))
    class _S3DownloadProgress:
        def __call__(self, bytes_amount):
            BackupService._broadcast_progress(
                backup_id_str, 'downloading', percent=min(10, bytes_amount / (1024 * 1024)),
                message=f'Downloading from cloud...',
                bytes_transferred=bytes_amount, total_bytes=0,
            )
    return download_from_s3(
        bucket, key, local_path,
        endpoint=endpoint, region=region,
        access_key=access_key, secret_key=secret_key,
        progress_callback=_S3DownloadProgress(),
    )


def _delete_backup_cloud_object(backup) -> bool:
    """Delete a backup's cloud object (S3/R2/MinIO).

    Resolves the cloud config via ``_resolve_cloud_config()`` and deletes
    the object. Returns ``True`` on success or if no cloud config exists.
    Logs a warning on failure but does not raise.
    """
    bucket, key, endpoint, region, access_key, secret_key = _resolve_cloud_config(backup)
    if not bucket or not key:
        return True  # Nothing to delete
    ok = delete_cloud_backup_object(
        bucket, key,
        endpoint=endpoint, region=region,
        access_key=access_key, secret_key=secret_key,
    )
    if ok:
        logger.info("Deleted cloud object s3://%s/%s for backup %s", bucket, key, backup.id)
    else:
        logger.warning("Failed to delete cloud object s3://%s/%s for backup %s", bucket, key, backup.id)
    return ok


def purge_user_backups(user_id) -> dict:
    """
    GDPR right-to-erasure helper.

    Must be invoked BEFORE ``Service`` rows for the user are deleted, while
    the CASCADE FK on ``ServiceBackup.service`` still resolves. Removes every
    backup artifact owned by the given user:
      * ``ServiceBackup.file_path`` tarballs on disk.
      * ``ServerBackup`` rows that included any of this user's services, and
        the tarball files referenced by them.
      * The matching S3/R2/MinIO object for any schedule with a configured
        cloud destination, derived from ``services_included``.

    Returns a dict of counters that callers can use for audit logging.
    """
    from apps.deployments.models_backup import (
        ServerBackup,
        ServiceBackup,
    )

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

        # Use stored cloud_key if available, otherwise fall back to schedule lookup
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

    # ServerBackups aren't tied to a single user; they reference a list of
    # service IDs in `services_included`. Use a JSON contains query
    # (PostgreSQL native) to filter at the DB level.  Falls back to
    # Python-side filtering on non-Postgres backends (SQLite in tests).
    from django.db.models import Q
    query = Q()
    for sid in user_service_ids:
        query |= Q(services_included__contains=[str(sid)])
    try:
        server_backups = list(ServerBackup.objects.filter(query))
    except Exception:
        # Database backend doesn't support JSON contains — fall back to
        # loading all ServerBackups and filtering in Python.
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
        # Also remove cloud object if server backup was uploaded
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

    return counters
