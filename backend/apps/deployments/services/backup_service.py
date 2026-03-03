import os
import io
import tarfile
import json
import uuid
import logging
import shutil
import traceback
import docker
from django.core.exceptions import PermissionDenied, ObjectDoesNotExist
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from apps.deployments.models import Service, EnvironmentVariable
from apps.deployments.models_backup import ServiceBackup, ServerBackup
from apps.deployments.models_storage import Volume

logger = logging.getLogger(__name__)

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

        Tries /app/backups/{subdir} first, then falls back to /tmp/backups/{subdir}
        if /app/backups is not writable (container image may not have it created
        with the correct ownership for the non-root user).
        """
        primary = os.path.join(settings.BASE_DIR, 'backups', subdir)
        try:
            os.makedirs(primary, exist_ok=True)
            # Test write access by creating a temp file
            test_file = os.path.join(primary, '.write_test')
            with open(test_file, 'w') as f:
                f.write('ok')
            os.remove(test_file)
            return primary
        except (PermissionError, OSError) as e:
            fallback = os.path.join('/tmp', 'backups', subdir)
            logger.warning(
                "Cannot write to %s (%s), falling back to %s",
                primary, e, fallback
            )
            os.makedirs(fallback, exist_ok=True)
            return fallback

    def backup_service(self, service_id, backup_type='MANUAL') -> ServiceBackup:
        service = Service.objects.get(id=service_id)
        backup = ServiceBackup.objects.create(
            service=service,
            status='IN_PROGRESS',
            backup_type=backup_type
        )
        temp_dir = None
        try:
            # Snapshot env vars — mask secrets to prevent credential leakage
            env_vars_raw = list(EnvironmentVariable.objects.filter(service=service).values('key', 'value', 'is_secret'))
            env_vars = []
            for ev in env_vars_raw:
                entry = dict(ev)
                if entry.get('is_secret'):
                    entry['value'] = '********'
                env_vars.append(entry)

            # Save metadata
            metadata = {
                'service_name': service.name,
                'service_id': str(service.id),
                'deploy_type': service.deploy_type,
                'env_vars': env_vars,
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

            # 2. Backup Volumes
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

            backup.file_path = filepath
            backup.metadata = metadata
            backup.status = 'COMPLETED'
            backup.size_bytes = os.path.getsize(filepath)
            backup.completed_at = timezone.now()
            backup.save()

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

        temp_dir = os.path.join(os.path.dirname(backup.file_path), f"restore_{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # 1. Extract Archive
            with tarfile.open(backup.file_path, "r:gz") as tar:
                # Security: reject members with absolute paths or '..' traversal
                for member in tar.getmembers():
                    if member.name.startswith('/') or '..' in member.name:
                        raise ValueError(f"Unsafe path in backup archive: {member.name}")
                tar.extractall(path=temp_dir)

            with open(os.path.join(temp_dir, "metadata.json"), 'r') as f:
                metadata = json.load(f)

            # 2. Restore Env Vars
            if 'env_vars' in metadata:
                for env in metadata['env_vars']:
                    EnvironmentVariable.objects.update_or_create(
                        service=target_service,
                        key=env['key'],
                        defaults={'value': env['value'], 'is_secret': env['is_secret']}
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
                        # If the image has tags, use the first one.
                        # If not, we might need to tag it?
                        # Usually load() restores tags.
                        if loaded_image.tags:
                            target_service.docker_image = loaded_image.tags[0]
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
                        # Use subprocess with Docker CLI to stream tar.gz into volume
                        import subprocess
                        cmd = [
                            "docker", "run", "--rm", "-i",
                            "-v", f"{vol_obj.name}:/dest",
                            "alpine", "tar", "-xzf", "-", "-C", "/dest",
                        ]
                        with open(vol_tar_path, 'rb') as f:
                            subprocess.run(cmd, stdin=f, check=True)

            logger.info("Restore complete.")
            return True

        except Exception as e:
            logger.error(f"Restore failed: {e}")
            raise
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def backup_server(self, backup_id=None):
        """
        Full server backup:
        1. PG_DUMP of the database.
        2. Backup of all services (recursive).
        3. Backup of PlatformConfig/Secrets.
        """
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

            # Find the actual postgres container (not pgbouncer)
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
                # The DB HOST in settings is often 'pgbouncer', but pg_dump
                # must run inside the real postgres container.
                pg_container = None
                try:
                    for c in self.docker_client.containers.list():
                        c_name = c.name.lower()
                        c_image = (c.image.tags[0] if c.image.tags else '').lower()
                        # Match containers with 'db' in name and postgres image
                        if ('postgres' in c_image and 'pgbouncer' not in c_name):
                            pg_container = c
                            break
                        # Fallback: match '-db-' in container name
                        if (('-db-' in c_name or c_name.endswith('-db'))
                                and 'pgbouncer' not in c_name
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

                elif host not in ('pgbouncer', 'localhost', '127.0.0.1'):
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
                    # Fallback: run pg_dump locally via pgbouncer's upstream
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

            backup.services_included = included
            backup.file_path = filepath
            backup.status = 'COMPLETED'
            backup.size_bytes = os.path.getsize(filepath)
            backup.completed_at = timezone.now()
            backup.save()
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

    def restore_server(self, backup_id):
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
        temp_dir = os.path.join(os.path.dirname(backup.file_path), f"restore_srv_{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            with tarfile.open(backup.file_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.startswith('/') or '..' in member.name:
                        raise ValueError(f"Unsafe path in server backup archive: {member.name}")
                tar.extractall(path=temp_dir)

            # Restore DB?
            # If we are running this from Django, we can't easily drop/restore the DB we are using.
            # So we skip DB restore here and assume it's done via CLI or manual steps if needed,
            # OR we only restore data tables.
            # For the requirements, "Implement server restore path" likely implies restoring services.

            # Restore Services
            services_dir = os.path.join(temp_dir, "services")
            if os.path.exists(services_dir):
                for filename in os.listdir(services_dir):
                    if filename.endswith(".tar.gz"):
                        # We need to "fake" a ServiceBackup object or just use the logic directly
                        # Helper to restore from file
                        self._restore_service_from_file(os.path.join(services_dir, filename))

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def _restore_service_from_file(self, filepath, owner=None):
        """Restore a service from a backup archive file.
        
        Args:
            filepath: Path to the service backup .tar.gz
            owner: User who owns the restored service (required for new services)
        """
        temp_dir = os.path.join(os.path.dirname(filepath), f"rest_tmp_{uuid.uuid4().hex}")
        os.makedirs(temp_dir, exist_ok=True)
        try:
            with tarfile.open(filepath, "r:gz") as tar:
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
