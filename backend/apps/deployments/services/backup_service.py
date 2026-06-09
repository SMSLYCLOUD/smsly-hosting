import os
import io
import tarfile
import json
import uuid
import logging
import shutil
import traceback
import docker
import tempfile
import hashlib
import base64
import binascii
import struct
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, hmac, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from apps.deployments.models import Service, EnvironmentVariable
from apps.deployments.models_backup import ServiceBackup, ServerBackup
from apps.deployments.models_storage import Volume

logger = logging.getLogger(__name__)

_CHUNKED_BACKUP_MAGIC = b"SMSLY-BACKUP-AESGCM-V1\n"
_CHUNKED_BACKUP_NONCE_PREFIX_BYTES = 8
_DEFAULT_CRYPTO_CHUNK_SIZE = 4 * 1024 * 1024
_FERNET_HEADER_SIZE = 1 + 8 + 16
_FERNET_HMAC_SIZE = 32

class BackupService:
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

        Tries /app/backups/{subdir} first (shared Docker volume in production),
        then falls back to the OS temp directory if not available.
        """
        primary = os.path.join('/app', 'backups', subdir)
        try:
            os.makedirs(primary, exist_ok=True)
            # Test write access by creating a temp file
            test_file = os.path.join(primary, '.write_test')
            with open(test_file, 'w') as f:
                f.write('ok')
            os.remove(test_file)
            return primary
        except (PermissionError, OSError) as e:
            fallback = os.path.join(tempfile.gettempdir(), 'backups', subdir)
            logger.warning(
                "Cannot write to %s (%s), falling back to %s",
                primary, e, fallback
            )
            os.makedirs(fallback, exist_ok=True)
            return fallback

    def _prepare_archive_for_restore(self, backup) -> tuple[str, str | None]:
        """Return readable tar.gz path, verifying checksum if backup object has metadata."""
        # Accept both a backup object or a raw filepath string
        if isinstance(backup, str):
            path = backup
            expected_hash = None
            expected_size = 0
        else:
            path = backup.file_path
            expected_hash = (backup.metadata or {}).get('checksum_sha256', '')
            expected_size = backup.size_bytes
        if not path or not os.path.exists(path):
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

        key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()
        if not key:
            raise ValueError("Encrypted backup detected but BACKUP_ENCRYPTION_KEY is not set.")
        decrypted_path = BackupService.decrypt_backup(path, key)
        return decrypted_path, decrypted_path

    def backup_service(self, service_id, backup_id=None, backup_type='MANUAL') -> ServiceBackup:
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
                    backup_type=backup_type
                )
        else:
            backup = ServiceBackup.objects.create(
                service=service,
                status='IN_PROGRESS',
                backup_type=backup_type
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
                'deploy_type': service.deploy_type,
                'env_vars': env_vars,
                'secrets_included': include_secret_values,
                'git_url': service.repository_url,
                'created_at': str(timezone.now()),
                'volumes': []
            }

            # Prepare directories
            backups_dir = self._get_backups_dir('services')

            # Temporary build directory for assembling the tarball
            temp_dir = os.path.join(backups_dir, f"tmp_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)

            # 1. Backup Docker Image
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

            if image_tag:
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

            # 2. Database dump (if container runs a DB — ensures consistent data)
            _dump_container_database(container_name, image_tag, temp_dir)

            # 3. Backup Volumes
            volumes = Volume.objects.filter(service=service)
            for vol in volumes:
                vol_filename = f"volume_{vol.name}.tar.gz"
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
                        logger.warning(f"Docker volume {vol.name} not found, skipping.")
                        continue

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
                    # Continue with other volumes (partial backup is better than none)

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
            backup.status = 'COMPLETED'
            backup.size_bytes = os.path.getsize(filepath)
            sha = hashlib.sha256()
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha.update(chunk)
            backup.metadata['checksum_sha256'] = sha.hexdigest()
            backup.completed_at = timezone.now()
            backup.save()
            self._prune_old_backups(ServiceBackup, service_id=service.id)

            # Upload to S3 if a scheduled backup with cloud config
            if backup_type == 'SCHEDULED':
                _upload_to_cloud_if_configured(service, filepath)

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

    def restore_service(self, backup_id, target_service_id=None, requesting_user_id=None):
        """
        Restore a service from backup.
        If target_service_id is provided, restore into that service (overwrite).
        Otherwise, restore into the original service.
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

        # Stop the running service to prevent data corruption during volume restore
        _stop_service_for_restore(target_service, is_remote)

        if is_remote:
            # Create a pre-restore backup snapshot
            logger.info(f"Creating pre-restore snapshot for remote service {target_service.name}")
            try:
                self.backup_service(target_service.id, backup_type='PRE_TRANSFER')
            except Exception as e:
                logger.warning(f"Failed to create pre-restore snapshot: {e}")

            archive_path, cleanup_archive = self._prepare_archive_for_restore(backup)
            temp_dir = os.path.join(os.path.dirname(archive_path), f"restore_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
            return self._restore_remote_service(backup, target_service, server_obj, temp_dir, archive_path, cleanup_archive)

        # Create a pre-restore backup snapshot to ensure we don't lose the active state in case of failure
        logger.info(f"Creating pre-restore snapshot for service {target_service.name}")
        try:
            self.backup_service(target_service.id, backup_type='PRE_TRANSFER')
        except Exception as e:
            logger.warning(f"Failed to create pre-restore snapshot: {e}")
            # We don't fail the restore if snapshot fails, but we log it

        archive_path, cleanup_archive = self._prepare_archive_for_restore(backup)
        temp_dir = os.path.join(os.path.dirname(archive_path), f"restore_{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # 1. Extract Archive
            with tarfile.open(archive_path, "r:gz") as tar:
                # Security: reject members with absolute paths or '..' traversal
                for member in tar.getmembers():
                    if member.name.startswith('/') or '..' in member.name:
                        raise ValueError(f"Unsafe path in backup archive: {member.name}")
                tar.extractall(path=temp_dir)

            with open(os.path.join(temp_dir, "metadata.json"), 'r') as f:
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
                            target_service.deploy_type = 'DOCKER' # Switch to docker deploy
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
                            command=["sleep", "3600"],
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

            logger.info("Restore complete. Queueing deployment.")
            from apps.deployments.models import Deployment
            from apps.deployments.tasks import enqueue_smart_deploy_task, _resolve_provider_for_service
            
            provider = _resolve_provider_for_service(target_service, prefer_local=True)
            if provider:
                deployment = Deployment.objects.create(
                    service=target_service,
                    status=Deployment.Status.QUEUED,
                    commit_hash='latest',
                    commit_message=f"Restored from backup {backup.id}",
                )
                
                # Ensure the service is properly marked as active before deployment
                target_service.status = Service.Status.ACTIVE
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
                logger.warning(f"Could not resolve provider to queue deployment for restored service {target_service.id}")
                # Ensure service is marked as active even if provider resolution fails
                target_service.status = Service.Status.ACTIVE
                target_service.save()
                
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
            out, err, code = ssh.exec_command(f"docker commit {service.name} {image_tag}")
            has_image = False
            if code == 0:
                logger.info(f"Committed remote container {service.name} to {image_tag}")
                # Save committed image to tar
                logger.info(f"Saving remote image {image_tag}...")
                out, err, code = ssh.exec_command(f"docker save -o {remote_image_path} {image_tag}")
                if code == 0:
                    has_image = True
                else:
                    logger.warning(f"Failed to save remote image: {err or out}")
            else:
                # If not running, fall back to configured docker_image
                if service.docker_image:
                    image_tag = service.docker_image
                    # Try to pull it on remote if not exists
                    ssh.exec_command(f"docker image inspect {image_tag} || docker pull {image_tag}")
                    logger.info(f"Saving remote image {image_tag}...")
                    out, err, code = ssh.exec_command(f"docker save -o {remote_image_path} {image_tag}")
                    if code == 0:
                        has_image = True
                    else:
                        logger.warning(f"Failed to save remote image: {err or out}")

            # 3. Backup Volumes remotely
            volumes = Volume.objects.filter(service=service)
            volumes_meta = []
            for vol in volumes:
                # Check if volume exists remotely
                out, err, code = ssh.exec_command(f"docker volume inspect {vol.name}")
                if code != 0:
                    logger.warning(f"Docker volume {vol.name} not found on remote server, skipping.")
                    continue

                vol_filename = f"volume_{vol.name}.tar.gz"
                remote_vol_path = f"{remote_temp_dir}/{vol_filename}"
                logger.info(f"Backing up remote volume {vol.name}...")
                
                # Compress remote volume using alpine helper container
                compress_cmd = (
                    f"docker run --rm -v {vol.name}:/volume_data:ro "
                    f"-v {remote_temp_dir}:/backup alpine:latest "
                    f"tar -czf /backup/{vol_filename} -C /volume_data ."
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
                    logger.error(f"Failed to backup remote volume {vol.name}: {err or out}")

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
                'env_vars': env_vars,
                'secrets_included': include_secret_values,
                'git_url': service.repository_url,
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

            logger.info(f"Downloading remote backup archive to local control plane...")
            ssh.download_file(remote_archive_path, local_filepath)

            # Optional encryption
            local_filepath = self._maybe_encrypt(local_filepath)

            # Save backup details
            backup.file_path = local_filepath
            backup.metadata = metadata
            backup.status = 'COMPLETED'
            backup.size_bytes = os.path.getsize(local_filepath)
            backup.completed_at = timezone.now()
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
        ssh.connect()

        if not ssh.check_docker():
            raise RuntimeError(f"Docker is not available on remote server {server.name}")

        try:
            # 1. Extract Archive locally to read metadata
            with tarfile.open(archive_path, "r:gz") as tar:
                # Security: reject members with absolute paths or '..' traversal
                for member in tar.getmembers():
                    if member.name.startswith('/') or '..' in member.name:
                        raise ValueError(f"Unsafe path in backup archive: {member.name}")
                tar.extractall(path=temp_dir)

            with open(os.path.join(temp_dir, "metadata.json"), 'r') as f:
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
            image_path = os.path.join(temp_dir, "image.tar")
            if os.path.exists(image_path):
                logger.info("Uploading Docker image to remote server...")
                remote_image_path = f"/tmp/image_{uuid.uuid4().hex[:8]}.tar"
                ssh.upload_file(image_path, remote_image_path)
                
                logger.info("Loading Docker image remotely...")
                out, err, code = ssh.exec_command(f"docker load -i {remote_image_path}")
                ssh.exec_command(f"rm -f {remote_image_path}") # clean up immediately
                if code != 0:
                    raise RuntimeError(f"Failed to load Docker image remotely: {err or out}")

                if metadata.get('docker_image'):
                    restored_image = metadata['docker_image']
                    target_service.docker_image = restored_image
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
                    ssh.exec_command(f"docker volume create {vol_obj.name}")

                    vol_tar_path = os.path.join(temp_dir, vol_meta['filename'])
                    if os.path.exists(vol_tar_path):
                        logger.info(f"Uploading volume archive {vol_meta['filename']} to remote...")
                        remote_vol_tar_path = f"/tmp/{vol_meta['filename']}"
                        ssh.upload_file(vol_tar_path, remote_vol_tar_path)

                        logger.info(f"Extracting volume {vol_obj.name} remotely...")
                        # Run helper container remotely to extract
                        extract_cmd = (
                            f"docker run --rm -v {vol_obj.name}:/dest "
                            f"-v /tmp:/src alpine:latest "
                            f"tar -xzf /src/{vol_meta['filename']} -C /dest"
                        )
                        out, err, code = ssh.exec_command(extract_cmd)
                        ssh.exec_command(f"rm -f {remote_vol_tar_path}") # clean up
                        if code != 0:
                            raise RuntimeError(f"Failed to extract volume remotely: {err or out}")

            logger.info("Remote restore complete. Queueing deployment.")
            from apps.deployments.models import Deployment
            from apps.deployments.tasks import enqueue_smart_deploy_task, _resolve_provider_for_service
            
            provider = _resolve_provider_for_service(target_service, prefer_local=False)
            if provider:
                deployment = Deployment.objects.create(
                    service=target_service,
                    status=Deployment.Status.QUEUED,
                    commit_hash='latest',
                    commit_message=f"Restored from backup {backup.id}",
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

    def backup_server(self, backup_id=None):
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
                backup = ServerBackup.objects.create(status='IN_PROGRESS')
        else:
            backup = ServerBackup.objects.create(status='IN_PROGRESS')
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

                    cmd = ["pg_dump", "-U", pg_user, pg_db]
                    res = pg_container.exec_run(cmd)
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
                        res = container.exec_run(["pg_dump", "-U", user, name])
                        if res.exit_code == 0:
                            with open(db_file, 'wb') as f:
                                f.write(res.output)
                        else:
                            raise Exception(f"pg_dump failed: {res.output}")
                    except Exception:
                        import subprocess
                        cmd = ["pg_dump", "-h", host, "-p", str(port), "-U", user, name]
                        with open(db_file, 'w') as f:
                            subprocess.run(cmd, env=env, stdout=f, check=True)
                else:
                    # Fallback: run pg_dump locally via pgcat's upstream
                    import subprocess
                    cmd = ["pg_dump", "-h", host, "-p", str(port), "-U", user, name]
                    with open(db_file, 'w') as f:
                        subprocess.run(cmd, env=env, stdout=f, check=True)

            except Exception as e:
                logger.warning(f"Database backup failed (skipping): {e}")
                # We might want to fail hard here in strict mode

            # 2. Services Backup
            services_dir = os.path.join(temp_dir, "services")
            os.makedirs(services_dir, exist_ok=True)
            services = Service.objects.all()
            included = []

            for service in services:
                try:
                    sb = self.backup_service(service.id)
                    included.append(str(sb.id))
                    # Move/Copy the service backup to our bundle
                    if sb.file_path and os.path.exists(sb.file_path):
                        shutil.copy2(sb.file_path, os.path.join(services_dir, os.path.basename(sb.file_path)))
                except Exception as e:
                    logger.error(f"Failed to backup service {service.name} during server backup: {e}")

            # 3. Platform Config
            # We can dump the .env file if it exists, or just serialize the PlatformConfig model
            # Dumping models is safer.
            from django.core import serializers
            from apps.deployments.models import PlatformConfig

            with open(os.path.join(temp_dir, "platform_config.json"), 'w') as f:
                data = serializers.serialize("json", PlatformConfig.objects.all())
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
            self._prune_old_backups(ServerBackup)
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

    def restore_server(self, backup_id, requesting_user_id=None):
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
                for member in tar.getmembers():
                    if member.name.startswith('/') or '..' in member.name:
                        raise ValueError(f"Unsafe path in server backup archive: {member.name}")
                tar.extractall(path=temp_dir)

            # Restore DB?
            # If we are running this from Django, we can't easily drop/restore the DB we are using.
            # So we skip DB restore here and assume it's done via CLI or manual steps if needed,
            # OR we only restore data tables.
            # For the requirements, "Implement server restore path" likely implies restoring services.

            # Check if this is a full server backup (services/ dir) or a single service backup
            services_dir = os.path.join(temp_dir, "services")
            metadata_path = os.path.join(temp_dir, "metadata.json")
            if os.path.exists(services_dir):
                for filename in os.listdir(services_dir):
                    if filename.endswith(".tar.gz"):
                        self._restore_service_from_file(os.path.join(services_dir, filename), owner=owner)
            elif os.path.exists(metadata_path):
                logger.info("Detected single-service backup archive. Restoring directly.")
                self._restore_service_from_file(archive_path, owner=owner)
            else:
                logger.warning("Server backup archive contains neither a services/ directory nor a metadata.json — nothing to restore.")

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            if cleanup_archive and os.path.exists(cleanup_archive):
                os.remove(cleanup_archive)

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
                for member in tar.getmembers():
                    if member.name.startswith('/') or '..' in member.name:
                        raise ValueError(f"Unsafe path in service backup archive: {member.name}")
                tar.extractall(path=temp_dir)

            with open(os.path.join(temp_dir, "metadata.json"), 'r') as f:
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
                    repository_url=metadata.get('git_url', '')
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

    def _maybe_encrypt(self, path: str) -> str:
        """
        Optionally encrypt backup archive at rest when BACKUP_ENCRYPTION_KEY is set.
        Uses chunked AES-GCM with the existing Fernet key material. Returns path
        to encrypted file and never loads the archive into memory.
        """
        key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()
        if not key:
            return path

        enc_path = path + ".enc"
        try:
            raw_key = self._decode_backup_key(key)
            aesgcm = AESGCM(raw_key)
            nonce_prefix = os.urandom(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
            chunk_size = self._crypto_chunk_size()

            with open(path, "rb") as source, open(enc_path, "wb") as encrypted:
                encrypted.write(_CHUNKED_BACKUP_MAGIC)
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
                encrypted.write(struct.pack(">I", 0))

            try:
                os.remove(path)
            except OSError:
                pass
            return enc_path
        except Exception as e:
            try:
                if os.path.exists(enc_path):
                    os.remove(enc_path)
            except OSError:
                pass
            logger.error(f"Encryption failed for {path}: {e}")
            return path

    @staticmethod
    def decrypt_backup(path: str, key: str) -> str:
        """
        Decrypt an encrypted backup to a temp file and return its path.
        Caller is responsible for deleting the temp file.
        Supports the current chunked AES-GCM format and legacy Fernet archives
        without loading the encrypted or decrypted backup into process memory.
        """
        with open(path, "rb") as source:
            magic = source.read(len(_CHUNKED_BACKUP_MAGIC))
        if magic == _CHUNKED_BACKUP_MAGIC:
            return BackupService._decrypt_chunked_backup(path, key)
        return BackupService._decrypt_legacy_fernet_backup(path, key)

    @staticmethod
    def _decrypt_chunked_backup(path: str, key: str) -> str:
        raw_key = BackupService._decode_backup_key(key)
        aesgcm = AESGCM(raw_key)

        fd, tmp_path = tempfile.mkstemp(prefix="backup_dec_", suffix=".tar.gz")
        os.close(fd)
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
            try:
                os.remove(tmp_path)
            except OSError:
                pass
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
            try:
                os.remove(token_path)
            except OSError:
                pass
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
        fd, tmp_path = tempfile.mkstemp(prefix="backup_dec_", suffix=".tar.gz")
        os.close(fd)
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
            raise ValueError("Failed to decrypt backup archive: invalid token") from e
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
        finally:
            try:
                os.remove(token_path)
            except OSError:
                pass

    @staticmethod
    def _prune_old_backups(model_cls, service_id=None):
        """Delete old backup records and their files.

        The retention count is controlled by the ``BACKUP_RETENTION_COUNT``
        environment variable (default ``5``).  For ``ServiceBackup`` the pruning
        is scoped to a single service; for ``ServerBackup`` it is global.
        Both the database rows *and* the associated backup files on disk are
        removed.
        """
        try:
            retain = int(os.environ.get("BACKUP_RETENTION_COUNT", "5"))
        except ValueError:
            retain = 5
        if retain < 1:
            retain = 1

        qs = model_cls.objects.order_by("-created_at")
        if service_id and hasattr(model_cls, "service_id"):
            qs = qs.filter(service_id=service_id)

        # Determine which IDs are older than the retention window
        ids_to_delete = list(qs.values_list("id", flat=True)[retain:])
        if not ids_to_delete:
            return

        # Delete files first so we don't lose the path after the DB row is gone
        for backup in model_cls.objects.filter(id__in=ids_to_delete):
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

        # Finally delete the DB rows
        model_cls.objects.filter(id__in=ids_to_delete).delete()


def repair_double_encrypted_env_vars(service_id: str | None = None) -> dict:
    """
    Detect and repair EnvironmentVariables corrupted by pre-fix backup/restore
    double-encryption. Returns {fixed: N, skipped: N} counts.
    """
    import base64
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


def _stop_service_for_restore(service, is_remote):


def backup_addon(addon_id: str) -> str | None:
    """Back up a single addon (Postgres/MySQL/Redis). Returns path to dump file or None."""
    from apps.deployments.models_addons import Addon
    import docker as _docker
    client = _docker.from_env()
    try:
        addon = Addon.objects.get(id=addon_id, status='ACTIVE')
        ctr = client.containers.get(addon.container_name or addon.name)
        atype = (addon.addon_type or '').lower()
        temp_dir = tempfile.mkdtemp(prefix=f"addon_backup_{addon_id}_")

        if 'postgres' in atype:
            dump_file = os.path.join(temp_dir, 'db_dump.sql')
            result = ctr.exec_run(['pg_dumpall', '-U', 'postgres', '-c'])
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                return dump_file
        elif 'mysql' in atype or 'mariadb' in atype:
            dump_file = os.path.join(temp_dir, 'db_dump.sql')
            result = ctr.exec_run(['mysqldump', '--all-databases', '-u', 'root'])
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                return dump_file
        elif 'redis' in atype:
            dump_file = os.path.join(temp_dir, 'redis_dump.rdb')
            ctr.exec_run(['redis-cli', 'SAVE'])
            time.sleep(1)
            bits, _ = ctr.get_archive('/data/dump.rdb')
            if bits:
                with open(dump_file, 'wb') as f:
                    for chunk in bits:
                        f.write(chunk)
                return dump_file
        elif 'mongo' in atype:
            dump_file = os.path.join(temp_dir, 'mongo_dump.archive')
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
    """Gracefully stop a running container before restoring volumes."""
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
            client.close()
        else:
            import docker as _docker
            client = _docker.from_env()
            try:
                ctr = client.containers.get(container_name)
                ctr.stop(timeout=30)
            except Exception:
                pass
        logger.info("Stopped service %s before restore", service.name)
    except Exception as exc:
        logger.warning("Could not stop service %s before restore: %s", service.name, exc)
    """Run pg_dump/mysqldump/redis SAVE inside a DB container for consistent backups."""
    import docker as _docker
    client = _docker.from_env()
    try:
        ctr = client.containers.get(container_name)
        image_lower = (image_tag or '').lower()
        dump_file = None

        if 'postgres' in image_lower:
            dump_file = os.path.join(temp_dir, 'db_dump.sql')
            env_vars = {
                'PGPASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
            }
            result = ctr.exec_run(
                ['pg_dumpall', '-U', os.environ.get('POSTGRES_USER', 'smsly_admin'), '-c'],
                environment=env_vars,
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                logger.info("pg_dumpall successful for %s", container_name)
            else:
                logger.warning("pg_dumpall failed: %s", result.output[-200:])

        elif 'mysql' in image_lower or 'mariadb' in image_lower:
            dump_file = os.path.join(temp_dir, 'db_dump.sql')
            password = os.environ.get('MYSQL_ROOT_PASSWORD', os.environ.get('MYSQL_PASSWORD', ''))
            result = ctr.exec_run(
                ['mysqldump', '--all-databases', '-u', 'root', f'-p{password}'],
            )
            if result.exit_code == 0:
                with open(dump_file, 'wb') as f:
                    f.write(result.output)
                logger.info("mysqldump successful for %s", container_name)

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


def upload_backup_to_s3(local_path: str, s3_bucket: str, s3_key: str,


def _upload_to_cloud_if_configured(service, filepath):
    """If the service has a BackupSchedule with S3 config, upload the backup."""
    try:
        from apps.deployments.models_backup import BackupSchedule
        sched = BackupSchedule.objects.filter(
            service=service, enabled=True, storage_backend='s3',
        ).first()
        if not sched or not sched.s3_bucket or not sched.s3_access_key:
            return
        s3_key = f"smsly-backups/{service.name}/{os.path.basename(filepath)}"
        upload_backup_to_s3(
            filepath, sched.s3_bucket, s3_key,
            endpoint=sched.s3_endpoint, region=sched.s3_region,
            access_key=sched.s3_access_key, secret_key=sched.s3_secret_key,
        )
    except Exception as exc:
        logger.warning("Cloud upload skipped for %s: %s", service.name, exc)
                        endpoint: str = '', region: str = 'us-east-1',
                        access_key: str = '', secret_key: str = '') -> bool:
    """Upload a backup file to S3 (or R2/MinIO via custom endpoint)."""
    try:
        import boto3
        kwargs = {'aws_access_key_id': access_key, 'aws_secret_access_key': secret_key,
                  'region_name': region}
        if endpoint:
            kwargs['endpoint_url'] = endpoint
        s3 = boto3.client('s3', **kwargs)
        s3.upload_file(local_path, s3_bucket, s3_key)
        logger.info("Backup uploaded to s3://%s/%s", s3_bucket, s3_key)
        return True
    except ImportError:
        logger.warning("boto3 not available — S3 upload skipped")
    except Exception as exc:
        logger.error("S3 upload failed: %s", exc)
    return False
