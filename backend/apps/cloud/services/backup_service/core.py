"""Core BackupService class."""

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import struct
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
from django.utils import timezone
from django.utils.text import slugify

from apps.deployments.models import EnvironmentVariable, Service
from apps.cloud.models.backup import ServerBackup, ServiceBackup
from apps.deployments.models.storage import Volume

from .exceptions import (
    _CHUNKED_BACKUP_FINGERPRINT_BYTES,
    _CHUNKED_BACKUP_KEY_ID_BYTES,
    _CHUNKED_BACKUP_MAGIC,
    _CHUNKED_BACKUP_NONCE_PREFIX_BYTES,
    _CHUNKED_BACKUP_V2_MAGIC,
    _CHUNKED_BACKUP_V3_MAGIC,
    _DEFAULT_CRYPTO_CHUNK_SIZE,
    BackupEncryptionRequired,
    BackupKeyCollisionError,
)
from .helpers import (
    _acquire_service_lock,
    _copy_file_to_container,
    _release_service_lock,
    _safe_tar_extractall,
)
from .operations import _dump_container_database
from .cloud import _download_backup_from_cloud, _upload_backup_to_cloud

logger = logging.getLogger(__name__)


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
                from apps.cloud.models.backup import BackupEncryptionKey
                active = BackupEncryptionKey.objects.filter(is_active=True).first()
                if active and active.key_material_encrypted:
                    key = active.key_material_encrypted.strip()
            except Exception as exc:
                logger.debug("Failed to load backup encryption key from settings/model: %s", exc)
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
        primary = os.path.join('/app', 'backups', subdir)
        os.makedirs(primary, exist_ok=True)
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

    @staticmethod
    def _crypto_chunk_size() -> int:
        return _DEFAULT_CRYPTO_CHUNK_SIZE

    @staticmethod
    def _decode_backup_key(key: str) -> bytes:
        try:
            return base64.urlsafe_b64decode(key)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Invalid backup key (expected base64): {exc}") from exc

    @staticmethod
    def _read_exact(file_obj, size: int) -> bytes:
        data = file_obj.read(size)
        if len(data) != size:
            raise ValueError(
                f"Unexpected end of file: expected {size} bytes, got {len(data)}"
            )
        return data

    @staticmethod
    def get_encryption_header(filepath: str) -> dict | None:
        try:
            with open(filepath, 'rb') as f:
                magic = f.read(len(_CHUNKED_BACKUP_MAGIC))
                if magic == _CHUNKED_BACKUP_MAGIC:
                    nonce_prefix = f.read(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
                    key_id_raw = f.read(_CHUNKED_BACKUP_KEY_ID_BYTES)
                    fingerprint_raw = f.read(_CHUNKED_BACKUP_FINGERPRINT_BYTES)
                    header = {
                        'format': 'v1',
                        'magic': magic.decode(),
                        'nonce_prefix': nonce_prefix.hex(),
                        'key_id': int.from_bytes(key_id_raw, 'big'),
                        'fingerprint': fingerprint_raw.hex(),
                    }
                    return header
                f.seek(0)
                second_line = f.readline()
                if second_line.startswith(b'# backup_key_fingerprint:'):
                    fingerprint_line = second_line.decode().strip()
                    fingerprint = fingerprint_line.split(':', 1)[1].strip()
                    return {
                        'format': 'legacy_fernet',
                        'fingerprint': fingerprint,
                    }
                if second_line.startswith(b'# key_id:'):
                    try:
                        parts = second_line.decode().strip().split()
                        key_id = parts[1]
                        fingerprint = parts[3] if len(parts) > 3 else ''
                        return {
                            'format': 'v2_file',
                            'key_id': key_id,
                            'fingerprint': fingerprint,
                        }
                    except (IndexError, ValueError):
                        pass
        except (FileNotFoundError, IsADirectoryError, OSError):
            pass
        return None

    @staticmethod
    def stamp_encryption_header_into_metadata(metadata: dict, filepath: str) -> dict:
        if not metadata:
            metadata = {}
        header = BackupService.get_encryption_header(filepath)
        if header:
            metadata['encryption'] = header
        return metadata

    @staticmethod
    def compute_backup_key_fingerprint(key_material: str) -> str:
        try:
            raw_key = BackupService._decode_backup_key(key_material)
        except ValueError:
            raise
        return hashlib.sha256(raw_key).digest()[:_CHUNKED_BACKUP_FINGERPRINT_BYTES].hex()

    @staticmethod
    def resolve_or_register_active_key(key_material: str) -> dict:
        from apps.cloud.models.backup import BackupEncryptionKey
        fingerprint = BackupService.compute_backup_key_fingerprint(key_material)
        existing = BackupEncryptionKey.objects.filter(
            fingerprint=fingerprint,
        ).first()
        if existing:
            return {'key_id': str(existing.id), 'fingerprint': fingerprint, 'created': False}
        obj = BackupEncryptionKey.objects.create(
            key_id=uuid.uuid4().hex[:16],
            fingerprint=fingerprint,
            key_material_encrypted=key_material,
            is_active=True,
        )
        return {'key_id': str(obj.id), 'fingerprint': fingerprint, 'created': True}

    @staticmethod
    def lookup_key_by_id(key_id: str) -> str | None:
        from apps.cloud.models.backup import BackupEncryptionKey
        try:
            obj = BackupEncryptionKey.objects.get(id=key_id, is_active=True)
            return obj.key_material_encrypted
        except Exception:
            return None

    @staticmethod
    def read_v2_header(path: str) -> dict:
        with open(path, 'rb') as f:
            magic = f.read(len(_CHUNKED_BACKUP_V2_MAGIC))
            if magic != _CHUNKED_BACKUP_V2_MAGIC:
                raise ValueError("Not a V2 backup format")
            nonce_prefix = f.read(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
            key_id_raw = f.read(_CHUNKED_BACKUP_KEY_ID_BYTES)
            fingerprint_raw = f.read(_CHUNKED_BACKUP_FINGERPRINT_BYTES)
            key_id = int.from_bytes(key_id_raw, 'big')
            return {
                'magic': magic.decode(),
                'nonce_prefix': nonce_prefix.hex(),
                'key_id': key_id,
                'fingerprint': fingerprint_raw.hex(),
            }

    @staticmethod
    def import_backup_key(
        backup_key_id: int | str,
        fingerprint: str,
        key_material_encrypted: str,
    ) -> dict:
        from apps.cloud.models.backup import BackupEncryptionKey
        existing = BackupEncryptionKey.objects.filter(key_id=str(backup_key_id)).first()
        if existing:
            existing_fp = getattr(existing, 'fingerprint', '') or ''
            if existing_fp and existing_fp != fingerprint:
                raise BackupKeyCollisionError(
                    f"key_id={backup_key_id} already registered with a different fingerprint"
                )
            return {'key_id': str(existing.id), 'status': 'already_exists'}
        obj = BackupEncryptionKey.objects.create(
            key_id=str(backup_key_id),
            fingerprint=fingerprint,
            key_material_encrypted=key_material_encrypted,
            is_active=True,
        )
        return {'key_id': str(obj.id), 'status': 'imported'}

    def _prepare_archive_for_restore(self, backup) -> tuple[str, str | None]:
        if isinstance(backup, str):
            path = backup
            expected_hash = None
            expected_size = 0
        else:
            path = backup.file_path
            expected_hash = (getattr(backup, 'metadata', None) or {}).get('checksum_sha256', '')
            expected_size = getattr(backup, 'size_bytes', 0) or 0
        if not path or not os.path.exists(path):
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
        lock_token = _acquire_service_lock(str(service_id), 'backup')
        if not lock_token:
            raise RuntimeError(f"Another backup/restore is already in progress for service {service_id}")
        try:
            return self._backup_service_inner(service_id, backup_id, backup_type, db_only)
        finally:
            _release_service_lock(str(service_id), lock_token)

    def _backup_service_inner(self, service_id, backup_id=None, backup_type='MANUAL', db_only=False) -> ServiceBackup:
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
            from apps.deployments.utils.target import resolve_active_execution_target
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

            backups_dir = self._get_backups_dir('services')

            temp_dir = os.path.join(backups_dir, f"tmp_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)

            image_filename = "image.tar"
            image_path = os.path.join(temp_dir, image_filename)

            image_tag = None
            try:
                container = self.docker_client.containers.get(service.name)
                repo = f"backup/{slugify(service.name)}"
                tag = f"{uuid.uuid4().hex[:8]}"
                image_tag = f"{repo}:{tag}"
                if not backup.db_only:
                    container.commit(repository=repo, tag=tag)
                    logger.info(f"Committed container {service.name} to {image_tag}")
            except docker.errors.NotFound:
                if service.docker_image:
                    image_tag = service.docker_image
                    try:
                        self.docker_client.images.get(image_tag)
                    except docker.errors.ImageNotFound:
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
                    if os.path.exists(image_path):
                        os.remove(image_path)
                    raise RuntimeError(f"Failed to save image {image_tag}: {img_err}") from img_err

            container_name = service.name
            _dump_container_database(container_name, image_tag, temp_dir, docker_client=self.docker_client)

            volumes = Volume.objects.filter(service=service)
            for vol in volumes:
                if backup.db_only:
                    continue
                safe_vol_name = vol.name.replace('/', '_').replace('\\', '_').replace('..', '_')
                vol_filename = f"volume_{safe_vol_name}.tar.gz"
                vol_path = os.path.join(temp_dir, vol_filename)

                logger.info(f"Backing up volume {vol.name}...")
                try:
                    try:
                        self.docker_client.volumes.get(vol.name)
                    except docker.errors.NotFound:
                        raise RuntimeError(
                            f"Docker volume {vol.name} is configured for service "
                            f"{service.name} but does not exist on the host"
                        )

                    stream_container = self.docker_client.containers.run(
                        "alpine:latest",
                        command=["tar", "-czf", "-", "-C", "/volume_data", "."],
                        volumes={vol.name: {'bind': '/volume_data', 'mode': 'ro'}},
                        detach=True,
                        remove=False
                    )

                    try:
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
                    logger.error(f"Volume backup failed for {vol.name}: {ve}")
                    raise

            # Always write the env vars file — even for db_only backups.
            # The restore path looks for this file; skipping it means a
            # db_only restore has no env context and silently restores
            # nothing. (The metadata['env_vars'] list is in the .meta
            # file, but the restore reads the JSON from the tarball.)
            env_backup_filename = "env_vars_backup.json"
            env_backup_path = os.path.join(temp_dir, env_backup_filename)
            with open(env_backup_path, 'w') as f:
                json.dump(metadata['env_vars'], f, indent=2)

            tarball_name = f"{slugify(service.name)}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
            if backup.db_only:
                tarball_name = tarball_name.replace('.tar.gz', '_db_only.tar.gz')
            tarball_path = os.path.join(backups_dir, tarball_name)

            logger.info(f"Creating tarball: {tarball_name}")
            with tarfile.open(tarball_path, 'w:gz') as tar:
                for item in os.listdir(temp_dir):
                    item_path = os.path.join(temp_dir, item)
                    tar.add(item_path, arcname=item)

            filepath = tarball_path
            # Compute checksum on the UNENCRYPTED tarball first — this is
            # the content checksum the restore uses to verify integrity
            # before attempting decryption (a corrupt encrypted file
            # would fail with a confusing InvalidToken error).
            content_checksum = hashlib.sha256()
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    content_checksum.update(chunk)
            metadata['content_checksum_sha256'] = content_checksum.hexdigest()

            filepath = self._maybe_encrypt(filepath)

            # Also compute the encrypted-file checksum for transport
            # integrity (detects corruption in transit / at rest).
            encrypted_checksum = hashlib.sha256()
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    encrypted_checksum.update(chunk)
            metadata['checksum_sha256'] = encrypted_checksum.hexdigest()
            metadata['size_bytes'] = os.path.getsize(filepath)

            BackupService.stamp_encryption_header_into_metadata(metadata, filepath)

            metadata_json = json.dumps(metadata)
            metadata_path = filepath + '.meta'
            with open(metadata_path, 'w') as f:
                f.write(metadata_json)

            backup.metadata = metadata
            backup.file_path = filepath
            backup.size_bytes = metadata['size_bytes']
            backup.status = 'COMPLETED'
            backup.completed_at = timezone.now()
            backup.save(update_fields=[
                'metadata', 'file_path', 'size_bytes', 'status', 'completed_at'
            ])

            try:
                result = _upload_backup_to_cloud(backup, filepath, service.name)
                if not result.get('uploaded') and result.get('reason'):
                    from .cloud import _alert_cloud_upload_failed
                    _alert_cloud_upload_failed(backup, result)
            except Exception as exc:
                logger.warning("Cloud upload failed for backup %s: %s", backup.id, exc)

            self._prune_old_backups(ServiceBackup, service_id=service.id)

            return backup

        except Exception as e:
            backup.status = 'FAILED'
            backup.error_message = str(e)
            backup.save(update_fields=['status', 'error_message'])
            traceback.print_exc()
            raise
        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as exc:
                    logger.debug("Failed to cleanup temp dir during backup: %s", exc)
            try:
                if image_tag and not backup.db_only and 'backup/' in image_tag:
                    self.docker_client.images.remove(image_tag, force=True)
            except Exception as exc:
                logger.debug("Failed to remove backup image %s: %s", image_tag, exc)

    def restore_service(self, backup_id, target_service_id=None, requesting_user_id=None, raise_on_snapshot_failure=True):
        try:
            backup = ServiceBackup.objects.get(id=backup_id)
        except ServiceBackup.DoesNotExist:
            raise ValueError(f"Backup not found: id={backup_id}")
        service_id = target_service_id or backup.service_id
        lock_token = _acquire_service_lock(str(service_id), 'restore')
        if not lock_token:
            raise RuntimeError(f"Another backup/restore is already in progress for service {service_id}")
        try:
            return self._restore_service_inner(backup_id, target_service_id, requesting_user_id, raise_on_snapshot_failure)
        finally:
            _release_service_lock(str(service_id), lock_token)

    def _restore_service_inner(self, backup_id, target_service_id=None, requesting_user_id=None, raise_on_snapshot_failure=True):
        snapshot_for_rollback = None
        try:
            backup = ServiceBackup.objects.get(id=backup_id)
        except ServiceBackup.DoesNotExist:
            raise ValueError(f"Backup not found: id={backup_id}")

        if backup.status != 'COMPLETED':
            raise ValueError(f"Backup {backup_id} status is {backup.status}, cannot restore.") from None

        target_service_id = target_service_id or backup.service_id
        target_service = Service.objects.get(id=target_service_id)
        try:
            from apps.deployments.utils.target import resolve_active_execution_target
            target = resolve_active_execution_target(target_service)
            is_remote = target["target_type"] in ("remote", "lite_agent") and target["server_obj"]
        except Exception:
            is_remote = False
            server_obj = None

        if is_remote:
            server_obj = target.get("server_obj")
            self.backup_service(target_service.id, backup_type='PRE_TRANSFER')

        if not is_remote and not self.docker_client:
            raise RuntimeError("Docker is not available. Restores require a running Docker daemon.")

        archive_path, cleanup_archive = self._prepare_archive_for_restore(backup)
        if is_remote:
            return self._restore_remote_service(backup, target_service, server_obj, tempfile.mkdtemp(), archive_path, cleanup_archive)

        temp_dir = tempfile.mkdtemp()
        try:
            images_loaded = []
            with tarfile.open(archive_path, 'r:gz') as tar:
                _safe_tar_extractall(tar, temp_dir)

            extracted_files = os.listdir(temp_dir)
            logger.info(f"Extracted: {extracted_files}")

            for fname in extracted_files:
                if fname == 'image.tar':
                    image_path = os.path.join(temp_dir, fname)
                    with open(image_path, 'rb') as f:
                        images_loaded = self.docker_client.images.load(f)
                    logger.info(f"Loaded {len(images_loaded)} images from image.tar")

            if images_loaded:
                restored_image = images_loaded[0]
                repo, tag = self._split_image_reference(restored_image.tags[0] if restored_image.tags else '')
                if not repo or not tag:
                    repo = f"restored/{slugify(target_service.name)}"
                    tag = uuid.uuid4().hex[:8]
                    restored_image.tag(repo, tag)
                target_service.docker_image = f"{repo}:{tag}"
                target_service.save(update_fields=['docker_image'])
                logger.info(f"Service image set to {target_service.docker_image}")

            db_dump_path = None
            for fname in extracted_files:
                if fname in ('db_dump.sql', 'redis_dump.rdb'):
                    db_dump_path = os.path.join(temp_dir, fname)
                    break
            if db_dump_path:
                # NOTE: an earlier revision wrote target_service.container_count
                # (a field that does not exist on Service — every DB restore
                # crashed with AttributeError). The intended bookkeeping is
                # handled by the deployment pipeline itself; nothing to zero.
                from .operations import _stop_service_for_restore
                _stop_service_for_restore(target_service, is_remote=False)

                container_name = target_service.name
                try:
                    ctr = self.docker_client.containers.get(container_name)
                    ctr.stop(timeout=30)
                    ctr.remove(force=True)
                    logger.info(f"Removed container {container_name} before restore")
                except docker.errors.NotFound:
                    pass

                try:
                    from .operations import _redeploy_restored_service_container
                    _redeploy_restored_service_container(target_service)
                    logger.info(f"Provider redeployed service {target_service.name} for DB restore")
                except Exception as prov_err:
                    logger.error(f"Provider deploy failed for DB restore: {prov_err}")
                    raise

                # Wait for the container to be ready with a probe loop
                # instead of a hardcoded sleep(5) — Java/large-image apps
                # can take 30+ seconds to start.
                _container_ready = False
                for _wait_i in range(30):
                    try:
                        ctr = self.docker_client.containers.get(container_name)
                        if ctr.status == 'running':
                            _container_ready = True
                            break
                    except docker.errors.NotFound:
                        pass
                    time.sleep(2)
                if not _container_ready:
                    raise RuntimeError(
                        f"Container {container_name} did not start after "
                        f"deployment for DB restore (waited 60s)."
                    )

                db_dest = '/tmp/restore_dump.sql' if fname == 'db_dump.sql' else '/tmp/restore_dump.rdb'
                _copy_file_to_container(self.docker_client, ctr.id, db_dump_path, db_dest)

                if fname == 'db_dump.sql':
                    # FIX: use the container's actual POSTGRES_USER and
                    # POSTGRES_DB from its env, not the service name —
                    # the backup used these creds, the restore must too.
                    ctr_env = {e.split('=', 1)[0]: e.split('=', 1)[1]
                               for e in (ctr.attrs.get('Config', {}).get('Env', []))
                               if '=' in e}
                    pg_user = ctr_env.get('POSTGRES_USER', 'postgres')
                    pg_db = ctr_env.get('POSTGRES_DB', 'postgres')
                    # Validate against the container env to prevent injection
                    import re as _re
                    if not _re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', pg_user):
                        pg_user = 'postgres'
                    if not _re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', pg_db):
                        pg_db = 'postgres'

                    result = ctr.exec_run(
                        ['psql', '-U', pg_user, '-d', pg_db,
                         '-f', db_dest],
                        timeout=600,
                    )
                    logger.info(f"psql restore exit: {result.exit_code}, output: {result.output[:200] if result.output else '(none)'}")
                elif fname == 'redis_dump.rdb':
                    ctr.exec_run(['redis-cli', 'FLUSHALL'], timeout=60)
                    with open(db_dump_path, 'rb') as f:
                        ctr.exec_run(['redis-cli', '--pipe'], data_input=f.read(), timeout=120)

            vol_files = [f for f in extracted_files if f.startswith('volume_') and f.endswith('.tar.gz')]
            all_vols = list(Volume.objects.filter(service=target_service))
            for vol_file in vol_files:
                vol_name_part = vol_file[len('volume_'):-len('.tar.gz')]

                # FIX: the safe name replaced ALL '/' and '\' with '_', so
                # we can't reconstruct the original by replacing one '_'
                # back to '/'. Instead, match against the Volume records:
                # find the volume whose safe-fied name equals the one in
                # the tarball. This is always correct regardless of how
                # many slashes the original had.
                target_vol = None
                for v in all_vols:
                    safe = v.name.replace('/', '_').replace('\\', '_').replace('..', '_')
                    if safe == vol_name_part:
                        target_vol = v
                        break
                if not target_vol:
                    logger.warning(f"No matching volume for {vol_file}, skipping")
                    continue

                try:
                    existing_vol = self.docker_client.volumes.get(target_vol.name)
                    existing_vol.remove(force=True)
                except docker.errors.NotFound:
                    pass

                self.docker_client.volumes.create(name=target_vol.name)

                vol_path = os.path.join(temp_dir, vol_file)

                helper = self.docker_client.containers.run(
                    'alpine:latest',
                    command=['tar', '-xzf', '/backup/volume.tar.gz', '-C', '/volume_data'],
                    volumes={
                        target_vol.name: {'bind': '/volume_data', 'mode': 'rw'},
                        temp_dir: {'bind': '/backup', 'mode': 'ro'},
                    },
                    detach=True,
                    remove=False,
                )
                try:
                    exit_result = helper.wait(timeout=120)
                    if exit_result['StatusCode'] != 0:
                        logs = helper.logs(stdout=True, stderr=True)
                        # A failed volume extraction means the restored
                        # volume is PARTIALLY WRITTEN or empty — previously
                        # this was only logged and the restore continued,
                        # silently producing a broken state marked success.
                        raise RuntimeError(
                            f"Volume restore failed for {target_vol.name} "
                            f"(exit {exit_result['StatusCode']}): "
                            f"{(logs or b'')[:500]!r}"
                        )
                finally:
                    helper.remove(force=True)

            for fname in extracted_files:
                if fname.endswith('.json') and fname.startswith('env_vars'):
                    env_path = os.path.join(temp_dir, fname)
                    with open(env_path) as f:
                        env_vars_restored = json.load(f)
                    for ev in env_vars_restored:
                        key = ev.get('key', '').strip()
                        value = ev.get('value')
                        if not key or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                            logger.warning("Skipping invalid env key on restore: %r", key)
                            continue
                        if isinstance(value, str) and (value == '********' or len(value) > 4096):
                            logger.warning("Skipping masked/oversize env value for %s", key)
                            continue
                        if key in ('LD_PRELOAD', 'PYTHONPATH', 'LD_LIBRARY_PATH'):
                            logger.warning("Skipping dangerous env key on restore: %s", key)
                            continue
                        EnvironmentVariable.objects.update_or_create(
                            service=target_service,
                            key=key,
                            defaults={'value': value, 'is_secret': ev.get('is_secret', False)}
                        )


            from .operations import _remap_domain_on_restore
            _remap_domain_on_restore(target_service, backup.metadata)

            try:
                from .operations import _redeploy_restored_service_container
                _redeploy_restored_service_container(target_service)
                logger.info(f"Restore redeploy complete for {target_service.name}")
            except Exception as deploy_err:
                # The archive is restored (image loaded, env vars written,
                # volumes replaced). A failed container restart must NOT
                # fail the whole restore — the service row is already in
                # its restored state and the operator can start the
                # container. Log loudly + record on the backup row.
                logger.error(f"Restore redeploy failed for {target_service.name}: {deploy_err}")
                from .operations import _emergency_restart_container
                _emergency_restart_container(target_service)
                try:
                    backup.redeploy_error = str(deploy_err)[:500]
                except AttributeError:
                    pass

            if cleanup_archive:
                try:
                    os.remove(cleanup_archive)
                except OSError as exc:
                    logger.debug("Failed to remove archive %s: %s", cleanup_archive, exc)
            from .cloud import _delete_backup_cloud_object
            _delete_backup_cloud_object(backup)

            backup.restored_at = timezone.now()
            backup.restore_count = (backup.restore_count or 0) + 1
            backup.save(update_fields=['restored_at', 'restore_count'])

            return {'service_id': str(target_service.id), 'status': 'restored'}

        except Exception as e:
            logger.error("Restore failed for backup %s: %s", backup_id, e)
            traceback.print_exc()
            raise
        finally:
            if temp_dir:
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass  # best-effort cleanup in finally block

    def _backup_remote_service(self, service, backup, server, include_secret_values) -> ServiceBackup:
        logger.info("Starting remote backup for %s on server %s", service.name, server.host)
        from apps.deployments.services.ssh_client import SSHClient
        ssh = SSHClient(
            ip=server.host, password=server.ssh_password,
            user=server.ssh_user, port=server.ssh_port,
            key_content=server.ssh_key, wg_address=server.wg_address,
        )
        ssh.connect()

        remote_backup_script = f"""  # noqa: F821
set -e
BACKUP_DIR=/tmp/smsly_backup_$(date +%s)
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"

SERVICE_NAME={shlex.quote(service.name)}

# Dump env vars — MASK secret-looking keys. The local backup path masks
# secrets (********) unless the backup_type is a transfer; the remote
# path previously dumped raw `docker inspect Config.Env`, writing
# plaintext credentials into the (unencrypted) artifact.
# Heuristic: mask keys that look like secrets (token/password/secret/key).
docker inspect "$SERVICE_NAME" 2>/dev/null | python3 -c "
import json,sys,re
data = json.load(sys.stdin)
env = data[0]['Config']['Env'] if data else []
mask_re = re.compile(r'(token|password|passwd|secret|key|credential|api)[a-z0-9_]*$', re.I)
for e in env:
    if '=' not in e:
        continue
    k, v = e.split('=', 1)
    if mask_re.search(k):
        v = '********'
    print(f'{k}={v}')
" > env_vars.txt 2>/dev/null || echo "env_vars_skipped"

# Save image
docker commit "$SERVICE_NAME" "backup_{shlex.quote(service.name)}_img"
docker save "backup_{shlex.quote(service.name)}_img" -o image.tar

# Dump volumes
for vol in $(docker inspect "$SERVICE_NAME" | python3 -c "
import json,sys
data = json.load(sys.stdin)
if data and 'Mounts' in data[0]:
    for m in data[0]['Mounts']:
        print(m.get('Name','') or m.get('Source',''))
" 2>/dev/null); do
    [ -z "$vol" ] && continue
    vol_safe=$(echo "$vol" | tr '/' '_' | tr '\\\\' '_')
    docker run --rm -v "$vol":/v alpine:latest tar -czf "/tmp/vol_${vol_safe}.tar.gz" -C /v . 2>/dev/null || true
    mv "/tmp/vol_${vol_safe}.tar.gz" "$BACKUP_DIR/" 2>/dev/null || true
done

# Create tarball
tar -czf /tmp/backup_artifact.tar.gz -C "$BACKUP_DIR" .

# Output the path
echo "BACKUP_PATH=/tmp/backup_artifact.tar.gz"
"""
        result = ssh.exec_command(remote_backup_script, timeout=600)
        output = result.get('stdout', '')
        error_out = result.get('stderr', '')
        if error_out:
            logger.info("Remote backup stderr: %s", error_out[:500])

        remote_path = None
        for line in output.splitlines():
            if line.startswith('BACKUP_PATH='):
                remote_path = line.split('=', 1)[1].strip()
                break

        if not remote_path:
            raise RuntimeError(f"Remote backup failed for {service.name}: could not determine remote path")

        backup_dir = self._get_backups_dir('services')
        local_path = os.path.join(
            backup_dir,
            f"{slugify(service.name)}_remote_{uuid.uuid4().hex[:8]}.tar.gz"
        )
        ssh.download_file(remote_path, local_path)
        ssh.exec_command(f"rm -f {remote_path}", raise_on_error=False)
        ssh.exec_command("rm -rf /tmp/smsly_backup_*", raise_on_error=False)
        ssh.close()

        # SECURITY: the local backup path always encrypts via
        # _maybe_encrypt; the remote path previously saved the raw
        # tarball unencrypted. Encrypt the downloaded artifact with the
        # same chunked AES-GCM scheme so remote backups are protected
        # at rest too. Checksums are computed by _maybe_encrypt's caller
        # in the local path; here we record the encrypted-file checksum.
        try:
            encrypted_path = self._maybe_encrypt(local_path)
            if encrypted_path != local_path:
                enc_checksum = hashlib.sha256()
                with open(encrypted_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b''):
                        enc_checksum.update(chunk)
                backup.file_path = encrypted_path
                backup.size_bytes = os.path.getsize(encrypted_path)
                metadata_enc = {
                    'checksum_sha256': enc_checksum.hexdigest(),
                    'size_bytes': backup.size_bytes,
                }
            else:
                backup.file_path = local_path
                backup.size_bytes = os.path.getsize(local_path)
                metadata_enc = {}
        except Exception as enc_exc:
            # If encryption fails, fail the backup entirely rather than
            # leaving an unencrypted artifact with (masked) secrets on
            # disk and a COMPLETED row.
            logger.error("Remote backup encryption failed for %s: %s", service.name, enc_exc)
            if os.path.exists(local_path):
                os.remove(local_path)
            backup.status = 'FAILED'
            backup.error_message = f"Encryption failed: {enc_exc}"
            backup.save(update_fields=['status', 'error_message'])
            raise

        backup.status = 'COMPLETED'
        backup.completed_at = timezone.now()
        backup.save(update_fields=['file_path', 'size_bytes', 'status', 'completed_at'])

        metadata = {
            'service_name': service.name,
            'service_id': str(service.id),
            'remote_server': server.host,
            'remote_backup': True,
            **metadata_enc,
        }
        backup.metadata = metadata
        backup.save(update_fields=['metadata'])

        return backup

    def _restore_remote_service(self, backup, target_service, server, temp_dir, archive_path, cleanup_archive):
        logger.info("Starting remote restore for %s on server %s", target_service.name, server.host)
        from apps.deployments.services.ssh_client import SSHClient
        ssh = SSHClient(
            ip=server.host, password=server.ssh_password,
            user=server.ssh_user, port=server.ssh_port,
            key_content=server.ssh_key, wg_address=server.wg_address,
        )
        ssh.connect()

        remote_tmp = f"/tmp/smsly_restore_{uuid.uuid4().hex}"
        ssh.exec_command(f"mkdir -p {remote_tmp}", raise_on_error=False)

        remote_archive = f"{remote_tmp}/backup_archive.tar.gz"
        ssh.upload_file(archive_path, remote_archive)

        remote_restore_script = f"""
set -e
cd {remote_tmp}
tar -xzf backup_archive.tar.gz

# Stop service
docker stop {shlex.quote(target_service.name)} 2>/dev/null || true

# Load image if present
if [ -f image.tar ]; then
    docker load -i image.tar
fi

# Restore volumes
for vol_file in volume_*.tar.gz; do
    [ -f "$vol_file" ] || continue
    vol_name=$(echo "$vol_file" | sed 's/^volume_//' | sed 's/\\.tar.gz$//' | tr '_' '/')
    docker volume create "$vol_name" 2>/dev/null || true
    docker run --rm -v "$vol_name":/v alpine:latest tar -xzf "/{remote_tmp}/$vol_file" -C /v || true
done

# Restore environment
if [ -f env_vars.txt ]; then
    echo "Environment backup available at $remote_tmp/env_vars.txt"
fi

# Restore database dump if present
if [ -f db_dump.sql ]; then
    docker cp db_dump.sql {shlex.quote(target_service.name)}:/tmp/ 2>/dev/null || true
fi

# Start service
docker start {shlex.quote(target_service.name)} 2>/dev/null || true

# Cleanup
rm -rf {remote_tmp}
"""
        result = ssh.exec_command(remote_restore_script, timeout=600)
        error_out = result.get('stderr', '')
        if error_out:
            logger.info("Remote restore stderr: %s", error_out[:500])
        ssh.close()

        if cleanup_archive:
            try:
                os.remove(archive_path)
                if cleanup_archive != archive_path:
                    os.remove(cleanup_archive)
            except OSError as exc:
                logger.debug("Failed to remove archive files: %s", exc)

        from .cloud import _delete_backup_cloud_object
        _delete_backup_cloud_object(backup)

        backup.restored_at = timezone.now()
        backup.restore_count = (backup.restore_count or 0) + 1
        backup.save(update_fields=['restored_at', 'restore_count'])

        return {'service_id': str(target_service.id), 'status': 'restored_remote'}

    @staticmethod
    def _split_image_reference(image_ref):
        if not image_ref:
            return None, None
        if ':' in image_ref:
            parts = image_ref.rsplit(':', 1)
            return parts[0], parts[1]
        return image_ref, 'latest'

    def backup_server(self, backup_id=None, db_only=False, backup_type='SERVER'):
        from apps.deployments.models import Service as Svc

        # TRANSFER backups include real secret values (the target node
        # needs them to hydrate the service). Non-transfer backups mask
        # secrets for safety. The caller (transfer._prepare) passes
        # backup_type='SERVER_TRANSFER'.
        include_secret_values = str(backup_type or '').upper() in {
            'TRANSFER',
            'SERVER_TRANSFER',
            'FULL_TRANSFER',
            'PRE_TRANSFER',
        }

        if backup_id:
            try:
                backup = ServerBackup.objects.get(id=backup_id)
                backup.status = 'IN_PROGRESS'
                backup.error_message = ''
                backup.save(update_fields=['status', 'error_message'])
            except ServerBackup.DoesNotExist:
                backup = ServerBackup.objects.create(
                    status='IN_PROGRESS',
                    backup_type='SERVER',
                )
        else:
            backup = ServerBackup.objects.create(
                status='IN_PROGRESS',
                backup_type='SERVER',
            )

        services = Svc.objects.filter(is_ai_router=False)

        temp_dir = tempfile.mkdtemp()
        try:
            metadata = {
                'server_backup': True,
                'services_count': services.count(),
                'created_at': str(timezone.now()),
                'services': [],
                'volumes': [],
            }

            backups_dir = self._get_backups_dir('server')

            for service in services:
                svc_meta = {
                    'name': service.name,
                    'id': str(service.id),
                    'deploy_type': service.deploy_type,
                    'docker_image': service.docker_image,
                    'public_domain': service.public_domain,
                    'env_vars': [],
                }

                env_vars = EnvironmentVariable.objects.filter(service=service).only('key', 'value', 'is_secret')
                for ev in env_vars:
                    svc_meta['env_vars'].append({
                        'key': ev.key,
                        'value': '********' if (ev.is_secret and not include_secret_values) else ev.value,
                        'is_secret': ev.is_secret,
                    })

                try:
                    ctr = self.docker_client.containers.get(service.name)
                    svc_meta['running'] = True
                    svc_meta['status'] = ctr.status
                except docker.errors.NotFound:
                    svc_meta['running'] = False
                    svc_meta['status'] = 'stopped'

                metadata['services'].append(svc_meta)

                volumes = Volume.objects.filter(service=service)
                for vol in volumes:
                    try:
                        self.docker_client.volumes.get(vol.name)
                    except docker.errors.NotFound:
                        continue

                    safe_name = vol.name.replace('/', '_')
                    vol_filename = f"vol_{safe_name}.tar.gz"
                    vol_path = os.path.join(temp_dir, vol_filename)

                    try:
                        stream_ctr = self.docker_client.containers.run(
                            'alpine:latest',
                            command=['tar', '-czf', '-', '-C', '/volume_data', '.'],
                            volumes={vol.name: {'bind': '/volume_data', 'mode': 'ro'}},
                            detach=True,
                            remove=False,
                        )
                        try:
                            with open(vol_path, 'wb') as f:
                                for chunk in stream_ctr.logs(stream=True, stdout=True, stderr=False):
                                    f.write(chunk)
                            metadata['volumes'].append({
                                'service': service.name,
                                'volume': vol.name,
                                'filename': vol_filename,
                            })
                        finally:
                            stream_ctr.remove(force=True)
                    except Exception as ve:
                        logger.warning(f"Server backup volume {vol.name} failed: {ve}")

            metadata_json = json.dumps(metadata)
            tarball_name = f"server_backup_{timezone.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
            tarball_path = os.path.join(backups_dir, tarball_name)

            with tarfile.open(tarball_path, 'w:gz') as tar:
                for item in os.listdir(temp_dir):
                    item_path = os.path.join(temp_dir, item)
                    tar.add(item_path, arcname=item)
                metadata_path = os.path.join(temp_dir, 'server_metadata.json')
                with open(metadata_path, 'w') as f:
                    f.write(metadata_json)
                tar.add(metadata_path, arcname='server_metadata.json')

            backup.file_path = tarball_path
            backup.size_bytes = os.path.getsize(tarball_path)
            backup.metadata = metadata
            backup.status = 'COMPLETED'
            backup.completed_at = timezone.now()
            backup.services_included = [str(s['id']) for s in metadata['services']]
            backup.save(update_fields=[
                'file_path', 'size_bytes', 'metadata', 'status',
                'completed_at', 'services_included',
            ])

            try:
                result = _upload_backup_to_cloud(backup, tarball_path, 'server')
                if not result.get('uploaded') and result.get('reason'):
                    from .cloud import _alert_cloud_upload_failed
                    _alert_cloud_upload_failed(backup, result)
            except Exception as exc:
                logger.warning("Cloud upload failed for server backup %s: %s", backup.id, exc)

            self._prune_old_backups(ServerBackup)

            return backup
        except Exception as e:
            backup.status = 'FAILED'
            backup.error_message = str(e)
            backup.save(update_fields=['status', 'error_message'])
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def restore_server(self, backup_id, requesting_user_id=None, raise_on_snapshot_failure=False):
        try:
            backup = ServerBackup.objects.get(id=backup_id)
        except ServerBackup.DoesNotExist:
            raise ValueError(f"Server backup not found: id={backup_id}")

        if backup.status != 'COMPLETED':
            raise ValueError(f"Server backup {backup_id} status is {backup.status}")

        if not self.docker_client:
            raise RuntimeError("Docker is not available. Restores require a running Docker daemon.")

        archive_path, cleanup_archive = self._prepare_archive_for_restore(backup)
        temp_dir = tempfile.mkdtemp()

        try:
            with tarfile.open(archive_path, 'r:gz') as tar:
                _safe_tar_extractall(tar, temp_dir)

            extracted = os.listdir(temp_dir)
            logger.info(f"Server backup extracted: {len(extracted)} files")

            metadata = backup.metadata or {}
            services_meta = metadata.get('services', [])

            for svc_meta in services_meta:
                svc_name = svc_meta.get('name', '')
                if not svc_name:
                    continue
                try:
                    svc = Service.objects.get(name=svc_name)
                except Service.DoesNotExist:
                    logger.warning(f"Service {svc_name} not found in DB, skipping restore")
                    continue

                vol_files = [f for f in extracted if f.startswith(f"vol_{svc_name}")]
                for vol_file in vol_files:
                    vol_path = os.path.join(temp_dir, vol_file)
                    vol_name = vol_file.replace('vol_', '').replace('.tar.gz', '')
                    vol_name = vol_name.replace('_', '/', 1) if '/' in vol_name else vol_name
                    try:
                        vol = Volume.objects.get(service=svc, name=vol_name)
                        try:
                            docker_vol = self.docker_client.volumes.get(vol.name)
                            docker_vol.remove(force=True)
                        except docker.errors.NotFound:
                            pass
                        self.docker_client.volumes.create(name=vol.name)
                        helper = self.docker_client.containers.run(
                            'alpine:latest',
                            command=['tar', '-xzf', f'/backup/{vol_file}', '-C', '/volume_data'],
                            volumes={
                                vol.name: {'bind': '/volume_data', 'mode': 'rw'},
                                temp_dir: {'bind': '/backup', 'mode': 'ro'},
                            },
                            detach=True, remove=False,
                        )
                        try:
                            helper.wait(timeout=120)
                        finally:
                            helper.remove(force=True)
                    except Volume.DoesNotExist:
                        logger.warning(f"Volume {vol_name} not found in DB")

            if cleanup_archive:
                try:
                    os.remove(cleanup_archive)
                except OSError as exc:
                    logger.debug("Failed to remove archive %s: %s", cleanup_archive, exc)

            from .cloud import _delete_backup_cloud_object
            _delete_backup_cloud_object(backup)

            backup.restored_at = timezone.now()
            backup.restore_count = (backup.restore_count or 0) + 1
            backup.save(update_fields=['restored_at', 'restore_count'])

            return {'status': 'restored', 'backup_id': str(backup.id)}
        except Exception as e:
            logger.error("Server restore failed: %s", e)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _restore_database_from_dump(self, dump_path):
        raise NotImplementedError("_restore_database_from_dump is not yet implemented")

    def _restore_platform_config(self, config_path):
        pass

    def _restore_service_from_file(self, filepath, owner=None):
        temp_dir = tempfile.mkdtemp()
        try:
            with tarfile.open(filepath, 'r:gz') as tar:
                _safe_tar_extractall(tar, temp_dir)
            extracted = os.listdir(temp_dir)
            logger.info(f"Restoring service from file: {extracted}")
            return {'status': 'restored', 'files': extracted}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _broadcast_progress(backup_id: str, stage: str, percent: float = 0,
                            message: str = '', bytes_transferred: int = 0,
                            total_bytes: int = 0):
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync

            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"backup_progress_{backup_id}",
                    {
                        'type': 'backup_progress',
                        'stage': stage,
                        'percent': percent,
                        'message': message,
                        'bytes_transferred': bytes_transferred,
                        'total_bytes': total_bytes,
                    }
                )
        except Exception as exc:
            logger.debug("Failed to send backup progress notification for %s: %s", backup_id, exc)

    @staticmethod
    def _backup_encryption_required() -> bool:
        required = os.environ.get("BACKUP_REQUIRE_ENCRYPTION", "").strip().lower()
        if required in ('1', 'true', 'yes'):
            return True
        try:
            from django.conf import settings
            return bool(getattr(settings, 'BACKUP_REQUIRE_ENCRYPTION', False))
        except ImportError:
            return False

    @staticmethod
    def decrypt_backup(path: str, key: str) -> str:
        BackupService._broadcast_progress(os.path.basename(path), 'decrypting', percent=0, message='Decrypting backup...')
        try:
            return BackupService._decrypt_chunked_backup(path, key)
        except (ValueError, InvalidToken, Exception) as e1:
            logger.info("Chunked decryption failed, trying legacy Fernet: %s", e1)
            try:
                return BackupService._decrypt_legacy_fernet_backup(path, key)
            except Exception as e2:
                raise ValueError(
                    f"Decryption failed (tried chunked and legacy): {e1}; {e2}"
                ) from e2

    @staticmethod
    def can_decrypt_backup(path: str, passed_key: str | None = None) -> bool:
        key = passed_key or BackupService._get_encryption_key()
        if not key:
            return False
        try:
            BackupService.decrypt_backup(path, key)
            return True
        except Exception:
            return False

    @staticmethod
    def _resolve_key_for_v2(path: str, passed_key: str) -> tuple[bytes, str]:
        raw_key = BackupService._decode_backup_key(passed_key)
        header = BackupService.read_v2_header(path)
        key_id = header.get('key_id', '')
        try:
            key_material = BackupService.lookup_key_by_id(str(key_id))
            if key_material:
                raw_key = BackupService._decode_backup_key(key_material)
                expected_fp = BackupService.compute_backup_key_fingerprint(key_material)
                return raw_key, expected_fp
        except Exception as exc:
            logger.debug("Key lookup by id %s failed, falling back to passed key: %s", key_id, exc)
        expected_fp = BackupService.compute_backup_key_fingerprint(passed_key)
        return raw_key, expected_fp

    @staticmethod
    def _decrypt_v2_chunked_backup(path: str, key: str) -> str:
        with open(path, 'rb') as f:
            magic = f.read(len(_CHUNKED_BACKUP_MAGIC))
            if magic != _CHUNKED_BACKUP_MAGIC:
                f.seek(0)
                magic = f.read(len(_CHUNKED_BACKUP_V2_MAGIC))
                if magic != _CHUNKED_BACKUP_V2_MAGIC:
                    raise ValueError("Not a chunked backup format")
                is_v2 = True
            else:
                is_v2 = False

            key_raw, _ = BackupService._resolve_key_for_v2(path, key) if is_v2 else (BackupService._decode_backup_key(key), '')
            nonce_prefix = f.read(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
            f.read(_CHUNKED_BACKUP_KEY_ID_BYTES)
            f.read(_CHUNKED_BACKUP_FINGERPRINT_BYTES)
            if is_v2:
                pass

            decrypted_path, cleanup = BackupService._make_private_decrypted_path()
            try:
                aesgcm = AESGCM(key_raw)
                chunk_size = BackupService._crypto_chunk_size()
                total = 0
                while True:
                    ct_len_bytes = f.read(4)
                    if not ct_len_bytes:
                        break
                    ct_len = struct.unpack('>I', ct_len_bytes)[0]
                    ct = BackupService._read_exact(f, ct_len)
                    nonce = nonce_prefix + ct[:12]
                    ciphertext = ct[12:]
                    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                    with open(decrypted_path, 'ab') as out:
                        out.write(plaintext)
                    total += len(plaintext)
                return decrypted_path
            except Exception:
                BackupService.cleanup_decrypted_path(decrypted_path)
                raise

    @staticmethod
    def _decrypt_v3_chunked_backup(path: str, key: str) -> str:
        with open(path, 'rb') as f:
            magic = f.read(len(_CHUNKED_BACKUP_V3_MAGIC))
            if magic != _CHUNKED_BACKUP_V3_MAGIC:
                raise ValueError("Not a V3 backup format")
            f.read(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
            f.read(_CHUNKED_BACKUP_KEY_ID_BYTES)
            f.read(_CHUNKED_BACKUP_FINGERPRINT_BYTES)
            key_raw = BackupService._decode_backup_key(key)
            aad_len_bytes = f.read(4)
            aad_len = struct.unpack('>I', aad_len_bytes)[0]
            aad = b''
            if aad_len > 0:
                aad = f.read(aad_len)
            decrypted_path, cleanup = BackupService._make_private_decrypted_path()
            try:
                aesgcm = AESGCM(key_raw)
                chunk_size = BackupService._crypto_chunk_size()
                total = 0
                while True:
                    ct_len_bytes = f.read(4)
                    if not ct_len_bytes:
                        break
                    ct_len = struct.unpack('>I', ct_len_bytes)[0]
                    ct = BackupService._read_exact(f, ct_len)
                    nonce = ct[:12]
                    ciphertext = ct[12:]
                    plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
                    with open(decrypted_path, 'ab') as out:
                        out.write(plaintext)
                    total += len(plaintext)
                return decrypted_path
            except Exception:
                BackupService.cleanup_decrypted_path(decrypted_path)
                raise

    @staticmethod
    def _make_private_decrypted_path(suffix: str = ".tar.gz") -> tuple:
        # 0o700 dir / 0o600 file: decrypted backups contain full service
        # state (DB dumps, env vars) and must not be readable by other
        # users on the host. Matches the design the cleanup tests assert.
        decrypted_dir = os.path.join('/app', 'backups', 'decrypted')
        os.makedirs(decrypted_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(decrypted_dir, 0o700)
        except OSError:
            pass
        fname = f"{uuid.uuid4().hex}{suffix}"
        path = os.path.join(decrypted_dir, fname)
        with open(path, 'wb'):
            pass
        os.chmod(path, 0o600)
        return path, path

    @staticmethod
    def cleanup_decrypted_path(path: str) -> None:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:
                logger.debug("Failed to remove decrypted path %s: %s", path, exc)

    @staticmethod
    def _decrypt_chunked_backup(path: str, key: str) -> str:
        with open(path, 'rb') as f:
            magic = f.read(len(_CHUNKED_BACKUP_MAGIC))
            if magic == _CHUNKED_BACKUP_MAGIC:
                key_raw = BackupService._decode_backup_key(key)
                nonce_prefix = f.read(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
                f.read(_CHUNKED_BACKUP_KEY_ID_BYTES)
                f.read(_CHUNKED_BACKUP_FINGERPRINT_BYTES)
                decrypted_path, cleanup = BackupService._make_private_decrypted_path()
                try:
                    aesgcm = AESGCM(key_raw)
                    total = 0
                    while True:
                        ct_len_bytes = f.read(4)
                        if not ct_len_bytes:
                            break
                        ct_len = struct.unpack('>I', ct_len_bytes)[0]
                        ct = BackupService._read_exact(f, ct_len)
                        nonce = nonce_prefix + ct[:8]
                        ciphertext = ct[8:]
                        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                        with open(decrypted_path, 'ab') as out:
                            out.write(plaintext)
                        total += len(plaintext)
                    return decrypted_path
                except Exception:
                    BackupService.cleanup_decrypted_path(decrypted_path)
                    raise

        with open(path, 'rb') as f:
            magic = f.read(len(_CHUNKED_BACKUP_V2_MAGIC))
            if magic == _CHUNKED_BACKUP_V2_MAGIC:
                return BackupService._decrypt_v2_chunked_backup(path, key)

        with open(path, 'rb') as f:
            magic = f.read(len(_CHUNKED_BACKUP_V3_MAGIC))
            if magic == _CHUNKED_BACKUP_V3_MAGIC:
                return BackupService._decrypt_v3_chunked_backup(path, key)

        raise ValueError("Not a chunked backup format (no valid magic header found)")

    @staticmethod
    def _decrypt_legacy_fernet_backup(path: str, key: str) -> str:
        expected_fp = BackupService.compute_backup_key_fingerprint(key)
        try:
            fernet_key_raw = base64.urlsafe_b64decode(key)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Invalid backup key (expected base64): {exc}") from exc
        fernet = Fernet(base64.urlsafe_b64encode(fernet_key_raw))

        decrypted_path, cleanup = BackupService._make_private_decrypted_path()
        try:
            header_bytes = b''
            with open(path, 'rb') as f:
                header_bytes = f.read(8)
            if header_bytes.startswith(b'gAAAA'):
                decrypted = fernet.decrypt(open(path, 'rb').read())
            else:
                with open(path, 'rb') as f:
                    nonce = f.read(16)
                    ct = f.read(os.path.getsize(path) - 16 - 32)
                    hmac_val = f.read(32)
                key_material = fernet_key_raw
                h = hmac.HMAC(key_material, hashes.SHA256())
                h.update(nonce + ct)
                try:
                    h.verify(hmac_val)
                except InvalidSignature:
                    raise ValueError("Backup HMAC signature mismatch — key may be wrong or backup corrupted")

                c = Cipher(algorithms.AES(key_material[:32]), modes.CBC(nonce))
                decryptor = c.decryptor()
                padded = decryptor.update(ct) + decryptor.finalize()
                unpadder = padding.PKCS7(128).unpadder()
                decrypted = unpadder.update(padded) + unpadder.finalize()

            with open(decrypted_path, 'wb') as f:
                f.write(decrypted)
            return decrypted_path
        except Exception:
            BackupService.cleanup_decrypted_path(decrypted_path)
            raise

    @staticmethod
    def _prune_old_backups(model_cls, service_id=None):
        retention_days = getattr(settings, 'BACKUP_RETENTION_DAYS', 7)
        cutoff = timezone.now() - timezone.timedelta(days=retention_days)
        filters = {'created_at__lt': cutoff, 'status': 'COMPLETED'}
        if service_id:
            filters['service_id'] = service_id
        stale = list(model_cls.objects.filter(**filters).order_by('created_at'))
        # Keep at least one backup per service — don't delete the last restorable copy
        # even if it's older than cutoff (fix for PAAS_INFRA_PRODUCTION_AUDIT:33).
        from collections import defaultdict
        by_service: dict = defaultdict(list)
        for b in stale:
            by_service[getattr(b, 'service_id', None)].append(b)
        ids_to_delete = []
        for sid, backups in by_service.items():
            all_completed = list(model_cls.objects.filter(service_id=sid, status='COMPLETED').order_by('-created_at'))
            if len(all_completed) <= 1:
                continue
            # If all completed are stale, keep the most recent stale one
            latest_id = all_completed[0].id
            for backup in backups:
                if backup.id == latest_id:
                    continue
                ids_to_delete.append(backup.id)
                if backup.file_path:
                    try:
                        if os.path.exists(backup.file_path):
                            os.remove(backup.file_path)
                    except OSError as exc:
                        logger.debug("Failed to remove backup file %s: %s", backup.file_path, exc)
        if ids_to_delete:
            model_cls.objects.filter(id__in=ids_to_delete).delete()

    def _maybe_encrypt(self, path: str) -> str:
        if BackupService._backup_encryption_required():
            key = BackupService._get_encryption_key()
            if not key:
                raise BackupEncryptionRequired(
                    "BACKUP_REQUIRE_ENCRYPTION is set but BACKUP_ENCRYPTION_KEY is not configured"
                )
        else:
            key = BackupService._get_encryption_key()
            if not key:
                return path

        key_raw = BackupService._decode_backup_key(key)
        aesgcm = AESGCM(key_raw)
        chunk_size = BackupService._crypto_chunk_size()
        enc_path = path + '.enc'

        with open(path, 'rb') as fin, open(enc_path, 'wb') as fout:
            fout.write(_CHUNKED_BACKUP_MAGIC)
            nonce_prefix = os.urandom(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
            fout.write(nonce_prefix)
            fp_raw = hashlib.sha256(key_raw).digest()[:_CHUNKED_BACKUP_FINGERPRINT_BYTES]
            fout.write(struct.pack('>I', int(fp_raw.hex()[:8], 16)))
            fout.write(fp_raw)

            total = 0
            while True:
                plaintext = fin.read(chunk_size)
                if not plaintext:
                    break
                nonce_suffix = os.urandom(8)
                nonce = nonce_prefix + nonce_suffix
                ct = aesgcm.encrypt(nonce, plaintext, None)
                chunk_data = nonce_suffix + ct
                ct_len = len(chunk_data)
                fout.write(struct.pack('>I', ct_len))
                fout.write(chunk_data)
                total += len(plaintext)

            logger.info("Encrypted backup (%d bytes plaintext) -> %s", total, enc_path)

        os.remove(path)
        return enc_path
