import logging
import os
import json
import time
import tempfile
import requests
import shlex
import glob
import socket
import hashlib
import hmac
import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from .backup_service import BackupService
from .ssh_client import SSHClient
from ..models import Service, PlatformConfig, EnvironmentVariable
from ..models_storage import Volume

logger = logging.getLogger(__name__)

TRANSFER_LOG_LIMIT = 300_000
TRANSFER_ERROR_LIMIT = 4_000


def _command_text(result) -> str:
    """Normalize SSHClient output while tolerating older string-returning mocks."""
    if isinstance(result, tuple):
        stdout = result[0] if len(result) > 0 else ""
        stderr = result[1] if len(result) > 1 else ""
        return (stdout or "") + (("\n" + stderr) if stderr else "")
    return "" if result is None else str(result)


def _safe_service_name(name: str) -> str:
    """Sanitize service name to alphanumeric and basic safe chars only."""
    return re.sub(r'[^a-zA-Z0-9 _.-]', '', name)[:255]


def _safe_backup_basename(file_path: str) -> str:
    """Extract a safe filename from a backup path, preventing path traversal."""
    name = os.path.basename(file_path)
    name = re.sub(r'[^a-zA-Z0-9_.-]', '', name)
    return name[:255]


def _redact_transfer_text(text: str) -> str:
    """Keep persisted transfer logs useful without storing secrets."""
    if not text:
        return ""
    safe = str(text).replace("\x00", "")
    safe = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----***-----END PRIVATE KEY-----",
        safe,
        flags=re.DOTALL,
    )
    safe = re.sub(
        r"(?i)((?:TOKEN|SECRET|PASSWORD|KEY|DSN|DATABASE_URL|REDIS_URL|AMQP_URL|BROKER_URL|API_KEY)[A-Z0-9_]*=)([^\s\"']+)",
        r"\1***",
        safe,
    )
    safe = re.sub(
        r"(?i)((?:Authorization|X-API-Key|X-Auth-Token):\s*)(\S+)",
        r"\1***",
        safe,
    )
    safe = re.sub(
        r"(?:https?://)[^:/\s]+:[^@\s]+@",
        "***@",
        safe,
    )
    return safe


class ServerTransferService:
    def __init__(self, transfer):
        self.transfer = transfer
        self.ssh = None
        self._uploaded_remote_backup_path = None

    def _log(self, message):
        """Append a timestamped message to the transfer logs."""
        ts = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        line = _redact_transfer_text(f"[{ts}] {message}\n")
        combined = (self.transfer.logs or "") + line
        if len(combined) > TRANSFER_LOG_LIMIT:
            combined = (
                "--- Older transfer log output truncated to keep this record bounded ---\n"
                + combined[-TRANSFER_LOG_LIMIT:]
            )
        self.transfer.logs = combined
        self.transfer.save(update_fields=['logs'])
        logger.info("Transfer %s: %s", self.transfer.id, _redact_transfer_text(message))

    def _target_server_record(self):
        from ..models_core import ManagedServer

        target = str(self.transfer.target_server_ip or '').strip()
        if not target:
            return None
        return ManagedServer.objects.filter(Q(host=target) | Q(private_ip=target)).first()

    def _build_sync_auth_headers(self, body: bytes, path: str) -> dict:
        headers = {'Content-Type': 'application/json'}
        server = self._target_server_record()

        token = str(getattr(server, 'api_token', '') or '').strip() if server else ''
        if token:
            if token.lower().startswith(('bearer ', 'token ')):
                headers['Authorization'] = token
            elif token.startswith('smsly_'):
                headers['Authorization'] = f'Bearer {token}'
            else:
                headers['Authorization'] = f'Token {token}'

        secret = str(getattr(server, 'gateway_secret', '') or '').strip() if server else ''
        if not secret:
            secret = str(
                getattr(settings, 'GATEWAY_SECRET', '') or getattr(settings, 'SECRET_KEY', '')
            ).strip()

        if secret:
            timestamp = str(int(time.time()))
            body_hash = hashlib.sha256(body).hexdigest()
            payload = f"POST|{path}|{timestamp}|{body_hash}"
            signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            headers['X-Request-Timestamp'] = timestamp
            headers['X-Gateway-Signature-V2'] = signature

        return headers

    def _sync_target_dashboard(self):
        """Notify the target server of this incoming transfer for dashboard visibility."""
        try:
            path = "/api/v1/transfers/register-incoming/"
            server = self._target_server_record()
            if not server:
                self._log(f"Warning: No ManagedServer record found for {self.transfer.target_server_ip}. Sync skipped.")
                return

            from .remote_orchestrator import RemoteOrchestrator
            orch = RemoteOrchestrator(server)
            
            payload = {
                'source_ip': self.transfer.source_server_ip,
                'target_ip': self.transfer.target_server_ip,
                'transfer_type': self.transfer.transfer_type,
                'service_name': self.transfer.service.name if self.transfer.service else None
            }
            
            # RemoteOrchestrator._request handles Token/HMAC auth and auto-auth via SSH
            resp = orch._request("POST", path, payload=payload, timeout=10)
            
            if resp and resp.status_code in (200, 201):
                self._log("Target dashboard synchronized successfully.")
            else:
                code = resp.status_code if resp else "timeout"
                self._log(f"Warning: Could not sync target dashboard (HTTP {code}).")
        except Exception as e:
            self._log(f"Warning: Target dashboard sync skipped: {e}")

    def execute(self):
        """Run transfer pipeline with explicit stage transitions."""
        try:
            self._init_ssh()

            self.transfer.status = 'PREPARING'
            self.transfer.save(update_fields=['status'])
            self._sync_target_dashboard()
            self._prepare()

            self.transfer.status = 'UPLOADING'
            self.transfer.save(update_fields=['status'])
            self._upload()

            self.transfer.status = 'RESTORING'
            self.transfer.save(update_fields=['status'])
            self._restore()

            self.transfer.status = 'DNS_CUTOVER'
            self.transfer.save(update_fields=['status'])
            self._dns_cutover()

            self.transfer.status = 'VERIFYING'
            self.transfer.save(update_fields=['status'])
            self._verify()

            self._complete()
        except Exception as exc:
            self._handle_failure(exc)
        finally:
            if self.ssh:
                self.ssh.close()

    def _init_ssh(self):
        key = (self.transfer.target_ssh_key or '').strip()
        password = (self.transfer.target_ssh_password or '').strip()

        # Prefer password over key when both are present (avoids invalid key errors)
        has_key = bool(key) and key.startswith("-----BEGIN ")
        has_password = bool(password)

        if not has_key and not has_password:
            # Try falling back to ManagedServer credentials
            from ..models_core import ManagedServer
            server = ManagedServer.objects.filter(
                host=self.transfer.target_server_ip
            ).first()
            if server:
                pw = (server.ssh_password or '').strip()
                k = (server.ssh_key or '').strip()
                has_password = bool(pw)
                has_key = bool(k) and k.startswith("-----BEGIN ")
                if has_password:
                    password = pw
                elif has_key:
                    key = k

        if not has_key and not has_password:
            raise ValueError("Target SSH key or password is missing.")

        ssh_kwargs = {'ip': self.transfer.target_server_ip}
        if has_password:
            ssh_kwargs['password'] = password
        elif has_key:
            ssh_kwargs['key_content'] = key

        self.ssh = SSHClient(**ssh_kwargs)
        try:
            self.ssh.connect()
        except Exception as e:
            raise ConnectionError(f"Could not connect to target server: {e}") from e

    def _prepare(self):
        """Step 1: create source backup and provision target."""
        self._update(5, 'Pre-flight: checking target server...')

        # Pre-flight: verify Docker is available on target
        if not self.ssh.check_docker():
            self._update(8, 'Installing Docker on target server...')
            self.ssh.install_docker()
            time.sleep(5)
            if not self.ssh.check_docker():
                raise RuntimeError("Failed to install Docker on target server.")

        # Pre-flight: for SERVICE transfers, verify CloudNeuron backend is running
        if self.transfer.transfer_type == 'SERVICE':
            backend_container = self._find_remote_backend_container(required=False)
            if not backend_container:
                self._ensure_target_platform_started()
                backend_container = self._find_remote_backend_container(required=False)
            if not backend_container:
                raise RuntimeError(
                    "Grid backend container not found on target server. "
                    "Please install Grid on the target before transferring services."
                )
            self._wait_for_remote_backend_ready(backend_container)

        self._update(10, 'Creating backup on source server...')

        backup_svc = BackupService()
        if self.transfer.transfer_type == 'SERVICE':
            if not self.transfer.service:
                raise ValueError("Service ID required for SERVICE transfer.")
            backup = backup_svc.backup_service(
                self.transfer.service.id,
                backup_type='TRANSFER',
            )
            self.transfer.source_backup = backup
            self.transfer.save(update_fields=['source_backup'])
        else:
            backup = backup_svc.backup_server()
            self.transfer.source_server_backup = backup
            self.transfer.save(update_fields=['source_server_backup'])

    def _upload(self):
        """Step 2: upload backup to target."""
        self._update(40, 'Transferring backup to target server...')

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        if not backup or not backup.file_path:
            raise ValueError("Backup file not found.")

        local_path = backup.file_path
        temp_decrypted = None

        if local_path.endswith(".enc"):
            key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()
            if not key:
                raise ValueError("Encrypted backup detected but BACKUP_ENCRYPTION_KEY is not set.")
            temp_decrypted = BackupService.decrypt_backup(local_path, key)
            local_path = temp_decrypted

        remote_path = f"/tmp/{_safe_backup_basename(local_path)}"
        self._uploaded_remote_backup_path = remote_path

        try:
            self.ssh.upload_file(local_path, remote_path)
        finally:
            if temp_decrypted and os.path.exists(temp_decrypted):
                os.remove(temp_decrypted)

        if self.transfer.transfer_type == 'FULL':
            install_script = os.path.join(settings.BASE_DIR, '../install.sh')
            if os.path.exists(install_script):
                # Enforce checksum env for supply-chain safety
                checksum = os.environ.get("SMSLY_INSTALL_SCRIPT_SHA256", "").strip()
                if not checksum:
                    raise ValueError("SMSLY_INSTALL_SCRIPT_SHA256 is required for full-server transfer.")
                self.ssh.upload_file(install_script, "/tmp/install.sh")
                self.ssh.exec_command("chmod +x /tmp/install.sh")
                self.ssh.exec_command(
                    "actual=$(sha256sum /tmp/install.sh | awk '{print $1}'); "
                    f"[ \"$actual\" = {shlex.quote(checksum)} ] || "
                    "{ echo 'install.sh checksum mismatch' >&2; exit 44; }"
                )

            local_env_path = os.path.join(settings.BASE_DIR, '../.env')
            if os.path.exists(local_env_path):
                self.ssh.upload_file(local_env_path, "/tmp/.env.restore")

    def _restore(self):
        """Step 3: restore on target."""
        self._update(60, 'Restoring services on target server...')

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        backup_filename = _safe_backup_basename(backup.file_path)
        remote_backup_path = (
            self._uploaded_remote_backup_path
            or f"/tmp/{backup_filename}"
        )

        if self.transfer.transfer_type == 'SERVICE':
            self._restore_single_service(remote_backup_path)
        else:
            self._restore_full_server(remote_backup_path)

    def _restore_single_service(self, remote_backup_path):
        target_server = self._target_server_record()
        is_lite_agent = getattr(target_server, 'is_lite_agent', False) if target_server else False
        
        if is_lite_agent:
            self._restore_single_service_lite(remote_backup_path)
            return

        self._update(65, 'Uploading backup archive to remote Grid API container...')
        
        # 1. We must execute the restoration inside the remote server's Grid
        # backend container so it registers the Service in the remote database!
        # First, copy the tarball into the backend container.
        backend_container = self._find_remote_backend_container(required=True)

        safe_backend_container = shlex.quote(backend_container)
        container_backup_path = f"/tmp/transfer_backup_{self.transfer.id}.tar.gz"
        container_script_path = f"/tmp/restore_trigger_{self.transfer.id}.py"
        self.ssh.exec_command(
            f"docker cp {shlex.quote(remote_backup_path)} "
            f"{safe_backend_container}:{shlex.quote(container_backup_path)}"
        )

        self._update(75, 'Hydrating Service via remote Django ORM...')
        
        # 2. Generate a Python script that boots Django inside the remote container
        # and calls BackupService to properly inflate the database models and Volumes.
        owner_email = self.transfer.service.owner.email if self.transfer.service and self.transfer.service.owner else None
        
        restore_script = self._build_restore_trigger_script(owner_email, container_backup_path)
        script_path = f"/tmp/restore_trigger_{self.transfer.id}.py"
        import tempfile
        local_script = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
        try:
            local_script.write(restore_script)
            local_script.close()
            # Upload to host
            self.ssh.upload_file(local_script.name, script_path)
            # Copy into container
            self.ssh.exec_command(
                f"docker cp {shlex.quote(script_path)} "
                f"{safe_backend_container}:{shlex.quote(container_script_path)}"
            )
        finally:
            os.unlink(local_script.name)

        self._update(85, 'Running database and volume migrations on target...')
        # Execute the python script inside the remote container
        result = _command_text(self.ssh.exec_command(
            f"docker exec {safe_backend_container} python3 {shlex.quote(container_script_path)}"
        ))
        if "RESTORE_FAILED" in result or "ERROR:" in result:
            raise RuntimeError(f"Remote service hydration failed: {result}")

        self._load_service_image_on_target(remote_backup_path)
        self._remap_target_platform_env(backend_container)
            
        # Cleanup (Best effort, don't fail the transfer if rm fails due to permissions)
        self.ssh.exec_command(
            f"docker exec -u 0 {safe_backend_container} sh -lc "
            + shlex.quote(
                f"rm -f {shlex.quote(container_backup_path)} "
                f"{shlex.quote(container_script_path)} || true"
            ),
            raise_on_error=False
        )
        self.ssh.exec_command(
            f"rm -f {shlex.quote(script_path)} {shlex.quote(remote_backup_path)}",
            raise_on_error=False
        )

        self._update(90, 'Starting service container on target...')
        # After restoration, the container exists but is not running. 
        # We use the metadata from the source backup to generate the run command.
        metadata = self.transfer.source_backup.metadata
        run_cmd = self._generate_docker_run_command(self.transfer.service, metadata)
        self.ssh.exec_command(run_cmd)
        self._seed_target_deployment_record(backend_container, metadata)

    def _restore_single_service_lite(self, remote_backup_path):
        """Restore a single service on a Lite Agent target (no local database)."""
        self._update(65, 'Extracting backup metadata on remote server...')

        backend_container = self._find_remote_backend_container(required=True)
        safe_backend_container = shlex.quote(backend_container)
        container_backup_path = f"/tmp/transfer_backup_{self.transfer.id}.tar.gz"

        # Copy backup into backend container for metadata extraction
        self.ssh.exec_command(
            f"docker cp {shlex.quote(remote_backup_path)} "
            f"{safe_backend_container}:{shlex.quote(container_backup_path)}"
        )

        # Extract metadata.json from the backup inside the container
        extract_dir = f"/tmp/transfer_extract_{self.transfer.id}"
        self.ssh.exec_command(
            f"docker exec {safe_backend_container} mkdir -p {shlex.quote(extract_dir)}"
        )
        self.ssh.exec_command(
            f"docker exec {safe_backend_container} tar -xzf {shlex.quote(container_backup_path)} "
            f"-C {shlex.quote(extract_dir)} metadata.json"
        )

        # Read metadata
        metadata_result = _command_text(self.ssh.exec_command(
            f"docker exec {safe_backend_container} cat "
            f"{shlex.quote(extract_dir)}/metadata.json"
        ))
        try:
            metadata = json.loads(metadata_result)
        except json.JSONDecodeError:
            raise RuntimeError(
                f"Failed to parse backup metadata from target container"
            )

        self._update(75, 'Loading service image on target...')
        self._load_service_image_on_target(remote_backup_path)

        # Restore volumes
        volumes = metadata.get('volumes', [])
        if volumes:
            self._update(80, 'Restoring service volumes on target...')
            volume_tmp = f"/tmp/vol_restore_{self.transfer.id}"
            self.ssh.exec_command(f"mkdir -p {shlex.quote(volume_tmp)}")

            docker_root = _command_text(self.ssh.exec_command(
                "docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null "
                "|| echo '/var/lib/docker'); echo \"$docker_root\""
            )).strip()

            for vol_meta in volumes:
                vol_name = vol_meta.get('name', '')
                vol_filename = vol_meta.get('filename', '')
                if not vol_name or not vol_filename:
                    continue

                # Extract the volume's tar.gz from the main backup archive
                self.ssh.exec_command(
                    f"tar -xzf {shlex.quote(remote_backup_path)} -C "
                    f"{shlex.quote(volume_tmp)} {shlex.quote(vol_filename)} 2>/dev/null || true"
                )

                vol_tar_path = f"{volume_tmp}/{vol_filename}"

                if vol_name.startswith('/'):
                    # Host path bind mount
                    self.ssh.exec_command(f"mkdir -p {shlex.quote(vol_name)}")
                    self.ssh.exec_command(
                        f"tar -xzf {shlex.quote(vol_tar_path)} -C "
                        f"{shlex.quote(vol_name)} 2>/dev/null || true"
                    )
                else:
                    # Docker named volume — create and populate via host filesystem
                    self.ssh.exec_command(
                        f"docker volume create {shlex.quote(vol_name)} 2>/dev/null || true"
                    )
                    vol_data_dir = f"{docker_root}/volumes/{vol_name}/_data"
                    self.ssh.exec_command(f"mkdir -p {shlex.quote(vol_data_dir)}")
                    self.ssh.exec_command(
                        f"tar -xzf {shlex.quote(vol_tar_path)} -C "
                        f"{shlex.quote(vol_data_dir)} 2>/dev/null || true"
                    )

            # Cleanup volume temp dir
            self.ssh.exec_command(f"rm -rf {shlex.quote(volume_tmp)} || true")

        # Start service container
        self._update(90, 'Starting service container on target...')
        run_cmd = self._generate_docker_run_command(self.transfer.service, metadata)
        self.ssh.exec_command(run_cmd)

        # Cleanup backup artifacts on container and host
        self.ssh.exec_command(
            f"docker exec {safe_backend_container} rm -rf "
            f"{shlex.quote(extract_dir)} {shlex.quote(container_backup_path)} || true",
            raise_on_error=False
        )
        self.ssh.exec_command(
            f"rm -f {shlex.quote(remote_backup_path)} || true",
            raise_on_error=False
        )

    def _remap_target_platform_env(self, backend_container):
        """
        Replace source-local platform URLs that cannot resolve on the target.

        Service backups preserve env vars for faithful restores, but addon
        hostnames such as redis-<service> and postgres-<service> may only
        exist on the source server. If a restored platform URL host cannot
        resolve from the target backend, point common runtime URL keys at the
        target platform services instead.
        """
        if not self.transfer.service or not self.ssh:
            return

        service_name = _safe_service_name(self.transfer.service.name)
        payload = {'service_name': service_name}
        remap_code = """
import json
import os
import socket
from urllib.parse import urlparse
from apps.deployments.models import Service, EnvironmentVariable

payload = json.loads(%r)
svc = Service.objects.filter(name=payload["service_name"]).first()
platform_database_url = os.environ.get("DATABASE_URL", "").strip()
platform_redis_url = os.environ.get("REDIS_URL", "").strip()
if svc:
    url_remaps = {
        "DATABASE_URL": platform_database_url,
        "MARKETER_DATABASE_URL": platform_database_url,
        "REDIS_URL": platform_redis_url,
        "RATE_LIMIT_REDIS_URL": platform_redis_url,
        "CACHE_URL": platform_redis_url,
        "CELERY_BROKER_URL": platform_redis_url,
        "CELERY_RESULT_BACKEND": platform_redis_url,
    }
    for key, replacement_url in url_remaps.items():
        if not replacement_url:
            continue
        env = EnvironmentVariable.objects.filter(service=svc, key=key).first()
        value = str(env.value or "").strip() if env else ""
        parsed = urlparse(value)
        host = parsed.hostname
        should_remap = value == "********"
        if host and host not in {"redis", "localhost", "127.0.0.1"}:
            try:
                socket.getaddrinfo(host, parsed.port or 6379)
            except OSError:
                should_remap = True
        if should_remap:
            EnvironmentVariable.objects.update_or_create(
                service=svc,
                key=key,
                defaults={"value": replacement_url, "is_secret": True, "source": "SYSTEM"},
            )
""".strip() % json.dumps(payload)

        cmd = (
            f"docker exec -i {shlex.quote(backend_container)} "
            "python manage.py shell <<'PY'\n"
            f"{remap_code}\n"
            "PY"
        )
        try:
            self.ssh.exec_command(cmd, timeout=60, raise_on_error=False)
        except Exception as exc:
            logger.warning("Failed to remap target platform env vars: %s", exc)

    def _seed_target_deployment_record(self, backend_container, metadata):
        """Create a deployment row on the target so remote dashboards are seeded."""
        if not self.transfer.service or not self.ssh:
            return

        service_name = _safe_service_name(self.transfer.service.name)
        image_ref = (
            str((metadata or {}).get('docker_image') or '').strip()
            or str(self.transfer.service.docker_image or '').strip()
            or 'backup-restore'
        )

        try:
            container_id = _command_text(self.ssh.exec_command(
                "docker inspect -f '{{.Id}}' "
                f"{shlex.quote(service_name)}",
                raise_on_error=False,
            )).strip().splitlines()[-1]
        except Exception:
            container_id = ''

        payload = {
            'service_name': service_name,
            'image_ref': image_ref,
            'container_id': container_id,
            'source_node': str(self.transfer.source_server_ip or ''),
        }
        restore_code = """
import json
from django.utils import timezone
from apps.deployments.models import Service, Deployment

payload = json.loads(%r)
service_name = payload["service_name"]
svc = Service.objects.filter(name=service_name).first()
if svc:
    latest = Deployment.objects.filter(service=svc).order_by("-created_at").first()
    if not latest:
        now = timezone.now()
        container_id = payload.get("container_id") or None
        status = Deployment.Status.ACTIVE if container_id else Deployment.Status.FAILED
        Deployment.objects.create(
            service=svc,
            status=status,
            commit_hash=(payload.get("image_ref") or "backup-restore")[-40:],
            commit_message="Seeded from interserver backup restore on target server",
            build_logs=(
                "Seeded after backup restore. "
                f"Container: {container_id or 'missing'} "
                f"Image: {payload.get('image_ref') or 'unknown'}"
            ),
            container_id=container_id,
            started_at=now,
            finished_at=now,
            source_node=payload.get("source_node") or "",
            pipeline_stages=[
                {"name": "Backup restore", "status": "done", "duration": 0},
                {
                    "name": "Target container verification",
                    "status": "done" if container_id else "failed",
                    "duration": 0,
                },
            ],
        )
""".strip() % json.dumps(payload)

        cmd = (
            f"docker exec -i {shlex.quote(backend_container)} "
            "python manage.py shell <<'PY'\n"
            f"{restore_code}\n"
            "PY"
        )
        try:
            self.ssh.exec_command(cmd, timeout=60, raise_on_error=False)
        except Exception as exc:
            logger.warning("Failed to seed target deployment record: %s", exc)

    def _load_service_image_on_target(self, remote_backup_path):
        """Load the service image archive on the target Docker host.

        The remote Django restore hydrates database and volume state from inside
        the platform container. Loading the image from the host as well makes
        the final container start independent of the target container's Docker
        socket proxy behavior.
        """
        self._update(88, 'Loading service image on target Docker host...')
        extract_dir = f"/tmp/transfer_image_{self.transfer.id}"
        image_path = f"{extract_dir}/image.tar"
        metadata_path = f"{extract_dir}/metadata.json"
        read_image_ref = (
            "target_image=$(python3 -c "
            + shlex.quote(
                "import json,sys; "
                "print((json.load(open(sys.argv[1])).get('docker_image') or '').strip())"
            )
            + f" {shlex.quote(metadata_path)})"
        )
        load_image = (
            f"if [ -s {shlex.quote(image_path)} ]; then "
            f"load_output=$(docker load -i {shlex.quote(image_path)} 2>&1); "
            "printf '%s\\n' \"$load_output\"; "
            "loaded_ref=$(printf '%s\\n' \"$load_output\" | sed -n 's/^Loaded image: //p' | tail -n 1); "
            "loaded_id=$(printf '%s\\n' \"$load_output\" | sed -n 's/^Loaded image ID: //p' | tail -n 1); "
            "loaded_source=\"${loaded_ref:-$loaded_id}\"; "
            "if [ -n \"$target_image\" ] && [ -n \"$loaded_source\" ] "
            "&& ! docker image inspect \"$target_image\" >/dev/null 2>&1; then "
            "docker tag \"$loaded_source\" \"$target_image\"; "
            "fi; "
            "else echo 'No image.tar found in backup archive'; fi"
        )
        cmd = " && ".join([
            f"rm -rf {shlex.quote(extract_dir)}",
            f"mkdir -p {shlex.quote(extract_dir)}",
            f"tar -xzf {shlex.quote(remote_backup_path)} -C {shlex.quote(extract_dir)} metadata.json",
            (
                f"tar -xzf {shlex.quote(remote_backup_path)} -C {shlex.quote(extract_dir)} "
                "image.tar || true"
            ),
            read_image_ref,
            load_image,
            f"rm -rf {shlex.quote(extract_dir)}",
        ])
        self.ssh.exec_command(cmd, timeout=1200)

    def _find_remote_backend_container(self, required=False):
        """Return the best matching Grid backend container name on the target."""
        configured = getattr(
            settings, "REMOTE_BACKEND_CONTAINER_NAME", "smsly-hosting-backend-1"
        )
        candidates = []

        for cmd in (
            "docker ps --filter name=backend --format '{{.Names}}'",
            f"docker ps --filter name={shlex.quote(configured)} --format '{{{{.Names}}}}'",
        ):
            output = _command_text(
                self.ssh.exec_command(cmd, raise_on_error=False)
            ).strip()
            for raw_name in output.splitlines():
                name = raw_name.strip("'\" ")
                if name and name not in candidates:
                    candidates.append(name)

        for name in candidates:
            if 'hosting' in name and 'backend' in name:
                return name
        for name in candidates:
            if 'backend' in name:
                return name

        if required:
            raise RuntimeError(
                "Could not locate Grid backend container on target server. "
                f"Searched for: {candidates or [configured]}"
            )
        return None

    def _ensure_target_platform_started(self):
        """Start an installed Grid target when Docker is up but the stack is down.

        Handles both full-platform nodes and Lite Agents (which use
        infrastructure/docker/docker-compose.agent-lite.yml).
        """
        hosting_path = self.ssh.find_hosting_path()
        safe_path = shlex.quote(hosting_path)
        timeout = int(getattr(settings, "TRANSFER_TARGET_START_TIMEOUT", 1200))
        agent_lite = "infrastructure/docker/docker-compose.agent-lite.yml"
        cmd = " && ".join([
            f"cd {safe_path}",
            "mkdir -p caddy-config /opt/smsly-cache",
            "docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null",
            "docker network inspect smsly-proxy >/dev/null 2>&1 || docker network create smsly-proxy >/dev/null",
            "("
            f"test -f {shlex.quote(agent_lite)} "
            f"&& docker compose -f {shlex.quote(agent_lite)} up -d --build"
            " || ("
            "test -f docker-compose.prod.yml "
            "&& docker compose -f docker-compose.prod.yml up -d --build"
            " || docker compose up -d --build"
            ")"
            ")",
        ])
        self._update(8, 'Starting Grid platform on target server...')
        self.ssh.exec_command(cmd, timeout=timeout)

    def _wait_for_remote_backend_ready(self, backend_container):
        """Wait until target platform health confirms backend dependencies.

        Uses docker exec inside the backend container so it works for both
        full-platform nodes and Lite Agents (which do not expose port 8000
        to the host).
        """
        safe_container = shlex.quote(backend_container)
        command = (
            f"for i in $(seq 1 60); do "
            f"docker exec {safe_container} curl -fsS -m 5 http://localhost:8000/health/live 2>/dev/null "
            f"| grep -q '\"status\": \"alive\"' "
            f"&& echo READY && exit 0; "
            f"docker exec {safe_container} curl -fsS -m 5 http://localhost:8000/health 2>/dev/null "
            f"| grep -q '\"status\": \"healthy\"' "
            f"&& echo READY && exit 0; "
            f"sleep 5; "
            f"done; echo NOT_READY; exit 1"
        )
        output = _command_text(self.ssh.exec_command(command, timeout=330))
        if "READY" not in output:
            raise RuntimeError("Target Grid backend did not become ready before restore.")

    def _target_hosting_path(self) -> str:
        """Find the remote Grid install path with a stable fallback."""
        try:
            path = self.ssh.find_hosting_path()
            if isinstance(path, str) and path.startswith("/"):
                return path.rstrip("/")
        except Exception as exc:
            logger.warning("Could not detect target Grid install path: %s", exc)
        return "/opt/smsly-hosting"

    @staticmethod
    def _build_restore_trigger_script(owner_email, backup_path='/tmp/transfer_backup.tar.gz'):
        return f"""import os
import sys
import time
import django
import logging
from urllib.parse import quote_plus

for candidate in (os.getcwd(), '/app', '/app/backend'):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

def configure_direct_database_url():
    current_url = os.environ.get('DATABASE_URL', '')
    # Skip override on agent/lite nodes — their DATABASE_URL already
    # points directly to the controller (no pgcat pooler to bypass).
    if current_url and 'pgcat' not in current_url:
        return
    user = os.environ.get('POSTGRES_USER')
    password = os.environ.get('POSTGRES_PASSWORD')
    db_name = os.environ.get('POSTGRES_DB')
    if user and password and db_name:
        os.environ['DATABASE_URL'] = (
            'postgresql://'
            + quote_plus(user)
            + ':'
            + quote_plus(password)
            + '@db:5432/'
            + quote_plus(db_name)
        )

configure_direct_database_url()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.services.backup_service import BackupService
from django.contrib.auth import get_user_model
from django.db import connections

logger = logging.getLogger(__name__)

def wait_for_database():
    last_error = None
    for attempt in range(30):
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            return
        except Exception as exc:
            last_error = exc
            connections.close_all()
            time.sleep(2)
    raise RuntimeError(f"Database did not become ready for restore: {{last_error}}")

def run_restore():
    wait_for_database()
    User = get_user_model()
    owner_email = {repr(owner_email)}
    
    # Precise owner matching: find user by email, fallback to superuser
    target_user = None
    if owner_email:
        target_user = User.objects.filter(email=owner_email).first()
        if target_user:
            print(f"Found matching owner on target: {{owner_email}}")
            
    if not target_user:
        target_user = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if target_user:
            print(f"No matching owner found. Assigning to fallback user: {{target_user.email}}")

    if not target_user:
        print("ERROR: No suitable user found on target server to own the restored service.", file=sys.stderr)
        sys.exit(1)
        
    try:
        svc = BackupService()
        # Restore and capture result
        svc._restore_service_from_file({repr(backup_path)}, owner=target_user)
        print("SUCCESS")
    except Exception as e:
        print(f"RESTORE_FAILED: {{str(e)}}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    run_restore()
"""

    def _restore_full_server(self, remote_backup_path):
        self._update(60, 'Installing Grid platform on target...')

        self.ssh.exec_command(
            "yes | NON_INTERACTIVE=1 bash /tmp/install.sh",
            timeout=3600,
        )
        hosting_path = self._target_hosting_path()
        quoted_hosting_path = shlex.quote(hosting_path)
        compose = (
            f"cd {quoted_hosting_path} && "
            "{ COMPOSE='docker compose'; "
            "docker compose version >/dev/null 2>&1 || COMPOSE='docker-compose'; "
            "$COMPOSE"
        )

        self._update(70, 'Stopping services for data restore...')
        self.ssh.exec_command(f"{compose} down -v; }}")

        self.ssh.exec_command(f"cp /tmp/.env.restore {quoted_hosting_path}/.env")

        remote_temp_dir = f"/tmp/restore_{self.transfer.id}"
        self.ssh.exec_command(f"mkdir -p {shlex.quote(remote_temp_dir)}")
        self.ssh.exec_command(f"tar -xzf {shlex.quote(remote_backup_path)} -C {shlex.quote(remote_temp_dir)}")

        self._update(75, 'Restoring database...')
        db_dump = f"{remote_temp_dir}/db_dump.sql"

        self.ssh.exec_command(f"{compose} up -d db; }}")
        time.sleep(20)

        self.ssh.exec_command(f"docker cp {shlex.quote(db_dump)} smsly-db:/tmp/dump.sql")

        db_user = _command_text(self.ssh.exec_command(
            f"grep POSTGRES_USER {quoted_hosting_path}/.env | cut -d= -f2"
        )).strip() or 'smsly'
        db_name = _command_text(self.ssh.exec_command(
            f"grep POSTGRES_DB {quoted_hosting_path}/.env | cut -d= -f2"
        )).strip() or 'smsly'

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", db_user):
            raise RuntimeError("Unsafe POSTGRES_USER value in target .env.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", db_name):
            raise RuntimeError("Unsafe POSTGRES_DB value in target .env.")

        drop_cmd = (
            f"{compose} exec -T db psql -U {shlex.quote(db_user)} postgres "
            f"-c 'DROP DATABASE IF EXISTS \"{db_name}\"; CREATE DATABASE \"{db_name}\";'"
            "; }"
        )
        self.ssh.exec_command(drop_cmd)

        restore_cmd = (
            f"{compose} exec -T db sh -c "
            + shlex.quote(
                f"psql -U {shlex.quote(db_user)} -d {shlex.quote(db_name)} < /tmp/dump.sql"
            )
            + "; }"
        )
        self.ssh.exec_command(restore_cmd)

        self._update(80, 'Restoring service data...')

        # Create restore script using list args to prevent shell injection
        restore_script = f"""
import os
import json
import subprocess
import glob

RESTORE_DIR = "{remote_temp_dir}"

def run(cmd):
    # Pass list directly to subprocess (shell=False)
    subprocess.run(cmd, check=True)

services_dir = os.path.join(RESTORE_DIR, "services")
if os.path.exists(services_dir):
    for tar_file in glob.glob(os.path.join(services_dir, "*.tar.gz")):
        print(f"Restoring {{tar_file}}...")
        svc_tmp = os.path.join(RESTORE_DIR, "svc_tmp")
        os.makedirs(svc_tmp, exist_ok=True)
        run(["tar", "-xzf", tar_file, "-C", svc_tmp])

        # Load Image
        run(["docker", "load", "-i", f"{{svc_tmp}}/image.tar"])

        # Volumes
        meta_path = os.path.join(svc_tmp, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                data = json.load(f)
            for vol in data.get('volumes', []):
                vname = vol['name']
                vfile = vol['filename']
                print(f"Restoring volume {{vname}}...")
                try:
                    run(["docker", "volume", "create", vname])
                except:
                    pass

                run([
                    "docker", "run", "--rm", "-i",
                    "-v", f"{{vname}}:/dest",
                    "-v", f"{{svc_tmp}}:/src",
                    "alpine", "tar", "-xzf", f"/src/{{vfile}}", "-C", "/dest"
                ])

        run(["rm", "-rf", svc_tmp])
"""
        script_path = f"/tmp/restore_{self.transfer.id}.py"
        local_script = tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', prefix=f'restore_{self.transfer.id}_', delete=False
        )
        try:
            local_script.write(restore_script)
            local_script.close()
            self.ssh.upload_file(local_script.name, script_path)
        finally:
            os.unlink(local_script.name)

        self.ssh.exec_command(f"python3 {shlex.quote(script_path)}")

        self._update(90, 'Starting platform...')
        self.ssh.exec_command(f"{compose} up -d; }}")

        self.ssh.exec_command(f"rm -rf {remote_temp_dir} {remote_backup_path} {script_path} /tmp/.env.restore")

    def _dns_cutover(self):
        self._update(85, 'DNS cutover: updating records...')

        target_ip = self.transfer.target_server_ip
        config = PlatformConfig.load()

        if config.cloudflare_api_token and config.domain:
            try:
                if self.transfer.transfer_type == 'FULL':
                    self._update_cloudflare_dns(config.domain, target_ip, config.cloudflare_api_token)
                elif self.transfer.service and self.transfer.service.public_domain:
                    target_server = self._target_server_record()
                    is_lite = getattr(target_server, 'is_lite_agent', False) if target_server else False
                    # For lite agents, create a per-service A record so the domain
                    # resolves directly to the target (Traefik routes via labels).
                    # For full platform targets, keep DNS pointing at the master
                    # and use WireGuard routing through the master's Caddy.
                    if is_lite:
                        self._update_service_a_record(
                            self.transfer.service.public_domain,
                            target_ip,
                            config.cloudflare_api_token,
                        )
            except Exception as e:
                logger.error(f"Cloudflare update failed: {e}")

        if self.transfer.transfer_type == 'FULL':
            config.server_ip = target_ip
            config.save()

        # Caddyfile regeneration removed from here — at this point service.server
        # is still the source (primary), so _remote_upstream_url_for_service()
        # returns empty, causing routing to local Traefik where the container
        # may no longer exist.  The correct routing (via WireGuard mesh IP) is
        # applied in _complete() after service.server is set to the target.

    def _interconnect_servers(self):
        """
        Automatically interconnect the source and target servers using the WireGuard Mesh.
        This enables them to discover and display services to each other.
        """
        from ..models_core import ManagedServer
        from ..models_mesh import MeshNetwork
        from .wireguard_service import WireGuardService

        self._update(96, 'Configuring mesh interconnect between source and target servers...')
        try:
            # 1. Look for or create a default mesh network
            mesh, created = MeshNetwork.objects.get_or_create(
                name="transfer-mesh",
                defaults={"subnet": "10.150.0.0/24"}
            )

            # 2. Add local (source) server to mesh if not present
            WireGuardService.add_peer_to_mesh(mesh, is_local=True)

            # 3. Add the target server as a ManagedServer (if not already managed)
            target_server = ManagedServer.objects.filter(host=self.transfer.target_server_ip).first()
            if not target_server:
                owner = None
                if self.transfer.service and self.transfer.service.owner:
                    owner = self.transfer.service.owner
                else:
                    from django.contrib.auth import get_user_model
                    owner = get_user_model().objects.first()

                if not owner:
                    raise ValueError("No valid user available to own the interconnected target server.")

                target_server = ManagedServer.objects.create(
                    name=f"TransferTarget-{self.transfer.target_server_ip}",
                    host=self.transfer.target_server_ip,
                    owner=owner,
                    ssh_key=self.transfer.target_ssh_key,
                    ssh_password=self.transfer.target_ssh_password,
                    status=ManagedServer.Status.ONLINE,
                )
            elif target_server.status != ManagedServer.Status.ONLINE:
                target_server.status = ManagedServer.Status.ONLINE
                target_server.save(update_fields=['status', 'updated_at'])
            if target_server and not (target_server.ssh_key or target_server.ssh_password):
                target_server.ssh_key = self.transfer.target_ssh_key
                target_server.ssh_password = self.transfer.target_ssh_password
                target_server.save(update_fields=['ssh_key', 'ssh_password', 'updated_at'])

            # 4. Add remote target server to mesh
            WireGuardService.add_peer_to_mesh(mesh, server=target_server, is_local=False)

            # 5. Deploy configurations to establish connection
            results = WireGuardService.deploy_full_mesh(mesh)
            if results.get("failed"):
                logger.warning(
                    "Mesh interconnect configured but deployment had failures: %s",
                    results["failed"],
                )
                return

            logger.info(f"Successfully configured mesh interconnect between local and {self.transfer.target_server_ip}")
        except Exception as e:
            # Non-fatal error, we still want the transfer to be marked complete
            logger.warning(f"Failed to interconnect servers via WireGuard mesh: {e}")

    def _verify(self):
        self._update(95, 'Verifying services on target server...')
        time.sleep(15)

        # Interconnect old and new servers automatically so they can communicate
        self._interconnect_servers()
        self._verify_between_servers()

        if self.transfer.transfer_type == 'FULL':
            url = f"http://{self.transfer.target_server_ip}:8000/health"
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code >= 500:
                    raise RuntimeError(
                        f"Target health check returned HTTP {resp.status_code}"
                    )
                logger.info("Target health check passed (HTTP %s)", resp.status_code)
            except requests.RequestException as e:
                logger.warning("Target health check failed: %s (non-fatal)", e)
        elif self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            # Check if the container is running on the target
            try:
                container_name = self.transfer.service.name
                result = _command_text(self.ssh.exec_command(
                    f"docker inspect -f '{{{{.State.Running}}}}' {shlex.quote(container_name)}"
                ))
                if result.strip() != 'true':
                    raise RuntimeError(
                        f"Service container {container_name} is not running on target"
                    )
                logger.info("Service container %s verified running on target", container_name)

                domain = str(self.transfer.service.public_domain or '').strip()
                if domain and not getattr(self.transfer.service, 'public_domain_hidden', False):
                    route_cmd = (
                        "code=$(curl -sS -o /dev/null -w '%{http_code}' "
                        f"-H {shlex.quote('Host: ' + domain)} "
                        "http://127.0.0.1/ 2>/dev/null || true); "
                        "echo SMSLY_ROUTE_HTTP:$code"
                    )
                    route_result = _command_text(
                        self.ssh.exec_command(route_cmd, raise_on_error=False)
                    )
                    match = re.search(r"SMSLY_ROUTE_HTTP:(\d{3}|000)", route_result)
                    route_code = match.group(1) if match else "000"
                    if route_code in {"000", "500", "502", "503", "504"}:
                        raise RuntimeError(
                            f"Target Traefik route for {domain} returned HTTP {route_code}"
                        )
                    logger.info(
                        "Service route %s verified through target Traefik (HTTP %s)",
                        domain,
                        route_code,
                    )
            except Exception as e:
                logger.warning("Service verification failed: %s", e)
                raise RuntimeError(f"Service verification failed: {e}") from e

    def _verify_between_servers(self):
        """
        Verify basic TCP reachability between source and target servers.

        This increases confidence that transfer-related operations and post-cutover
        server communication can work in both directions.
        """
        source_ip = str(getattr(self.transfer, 'source_server_ip', '') or '').strip()
        target_ip = str(getattr(self.transfer, 'target_server_ip', '') or '').strip()
        if not source_ip or not target_ip or not self.ssh:
            return

        # Local controller -> target SSH reachability
        try:
            with socket.create_connection((target_ip, 22), timeout=5):
                logger.info("Connectivity check passed: controller -> %s:22", target_ip)
        except OSError as exc:
            logger.warning("Connectivity check failed: controller -> %s:22 (%s)", target_ip, exc)

        # Target -> source SSH reachability
        tcp_check_cmd = (
            "bash -lc "
            + shlex.quote(
                f"timeout 5 sh -c '</dev/tcp/{source_ip}/22' >/dev/null 2>&1 "
                "&& echo TRANSFER_TCP_OK || echo TRANSFER_TCP_FAIL"
            )
        )
        remote_result = _command_text(self.ssh.exec_command(tcp_check_cmd)).strip()
        if "TRANSFER_TCP_OK" in remote_result:
            logger.info("Connectivity check passed: target -> %s:22", source_ip)
            return

        message = (
            f"Target server cannot reach source server on TCP/22 ({source_ip}). "
            "Verify firewall/security-group rules between both servers."
        )
        if bool(getattr(settings, "TRANSFER_REQUIRE_BIDIRECTIONAL_SSH", False)):
            raise RuntimeError(message)
        logger.warning(message)

    def _regenerate_master_caddyfile(self):
        """Regenerate and reload the Caddyfile on the master node.

        After a service is transferred to a remote node, the master's
        Caddyfile must be updated to route traffic for that service's
        domain to the remote node via WireGuard mesh instead of the
        local Traefik instance.
        """
        from services.caddy_manager import generate_caddyfile, apply_caddyfile
        config = PlatformConfig.load()
        content = generate_caddyfile(config)
        cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
        result = apply_caddyfile(content, cloudflare_token=cf_token)
        if result.get('ok'):
            self._log("Caddyfile regenerated on master node for remote service routing.")
        else:
            raise RuntimeError(
                f"Caddyfile regeneration failed after transfer: {result.get('message')}. "
                "Reverting transfer."
            )

    def _complete(self):
        self.transfer.status = 'COMPLETED'
        self.transfer.completed_at = timezone.now()
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=48)
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''

        if self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            from ..models_core import ManagedServer
            target_server = ManagedServer.objects.filter(
                Q(host=self.transfer.target_server_ip) |
                Q(private_ip=self.transfer.target_server_ip)
            ).first()
            if target_server:
                self.transfer.service.server = target_server
                self.transfer.service.active_target_type = (
                    'lite_agent'
                    if getattr(target_server, 'is_lite_agent', False)
                    else 'remote'
                )
                self.transfer.service.active_host_ip = (
                    getattr(target_server, 'wg_address', None)
                    or target_server.private_ip
                    or target_server.host
                    or self.transfer.target_server_ip
                )
                self.transfer.service.active_runtime_id = self.transfer.service.name
                self.transfer.service.save(
                    update_fields=[
                        'server',
                        'active_target_type',
                        'active_host_ip',
                        'active_runtime_id',
                    ]
                )

                # Regenerate Caddyfile on the master node so it knows to
                # route traffic for this service to the remote node via
                # WireGuard mesh.  Without this, Caddy proxies to the local
                # Traefik where the service doesn't exist,
                # causing HTTP 502 errors.
                self._regenerate_master_caddyfile()

        self.transfer.save()
        self._update(100, 'Transfer complete!')

    def rollback(self):
        if not self.transfer.can_rollback:
            raise ValueError('Rollback not allowed')
        if self.transfer.status != 'COMPLETED':
            raise ValueError('Rollback is only available for completed transfers')
        if self.transfer.rollback_deadline and timezone.now() > self.transfer.rollback_deadline:
            self.transfer.can_rollback = False
            self.transfer.save(update_fields=['can_rollback'])
            raise ValueError('Rollback window has expired')

        config = PlatformConfig.load()
        if config.cloudflare_api_token and config.domain:
            if self.transfer.transfer_type == 'FULL':
                self._update_cloudflare_dns(config.domain, self.transfer.source_server_ip, config.cloudflare_api_token)
            elif self.transfer.service and self.transfer.service.public_domain:
                target_server = self._target_server_record()
                is_lite = getattr(target_server, 'is_lite_agent', False) if target_server else False
                if is_lite:
                    self._delete_service_a_record(
                        self.transfer.service.public_domain,
                        config.cloudflare_api_token,
                    )

        if self.transfer.transfer_type == 'FULL':
            config.server_ip = self.transfer.source_server_ip
            config.save()
        elif self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            from ..models_core import ManagedServer
            source_server = ManagedServer.objects.filter(host=self.transfer.source_server_ip).first()
            self.transfer.service.server = source_server
            self.transfer.service.save(update_fields=['server'])

            # Regenerate Caddyfile so routing points back to the source
            self._regenerate_master_caddyfile()

        self.transfer.status = 'ROLLED_BACK'
        self.transfer.can_rollback = False
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''
        self.transfer.save()

    def _update(self, percent, step):
        self.transfer.progress_percent = percent
        self.transfer.current_step = step
        self.transfer.save(update_fields=['progress_percent', 'current_step'])
        self._log(step)

    def _handle_failure(self, error):
        self.transfer.status = 'FAILED'
        self.transfer.error_message = _redact_transfer_text(str(error))[:TRANSFER_ERROR_LIMIT]
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''
        self.transfer.save(update_fields=['status', 'error_message', 'target_ssh_key', 'target_ssh_password'])
        self._log(f"CRITICAL FAILURE: {error}")

    def _generate_docker_run_command(self, service, metadata):
        name = service.name
        image = metadata.get('docker_image') or service.docker_image
        if not image:
            raise RuntimeError(
                f"No Docker image was available in the backup for service {service.name}. "
                "Use remote Git deployment or provide service.docker_image for this service."
            )

        config = PlatformConfig.load()

        # Build command parts safely
        run_args = ["docker", "run", "-d", "--name", name, "--restart", "unless-stopped"]

        net = "smsly-net"
        run_args.extend(["--network", net])

        env_vars = metadata.get('env_vars', [])
        for e in env_vars:
            # -e KEY=VAL
            run_args.extend(["-e", f"{e['key']}={e['value']}"])

        domain = service.public_domain
        port = service.internal_port

        # Labels
        run_args.extend(["-l", "traefik.enable=true"])
        run_args.extend(["-l", f"traefik.docker.network={net}"])
        run_args.extend(["-l", f"traefik.http.routers.{name}.rule=Host(`{domain}`)"])
        run_args.extend(["-l", f"traefik.http.routers.{name}.service={name}"])
        run_args.extend(["-l", f"traefik.http.services.{name}.loadbalancer.server.port={port}"])

        enable_traefik_tls = (
            str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if config.use_ssl and enable_traefik_tls:
            run_args.extend(["-l", f"traefik.http.routers.{name}.entrypoints=websecure"])
            run_args.extend(["-l", f"traefik.http.routers.{name}.tls=true"])
            run_args.extend(["-l", f"traefik.http.routers.{name}.tls.certresolver=letsencrypt"])
        else:
            run_args.extend(["-l", f"traefik.http.routers.{name}.entrypoints=web"])
            if config.use_ssl:
                middleware_name = f"{name}-forwarded-https"
                run_args.extend(["-l", f"traefik.http.routers.{name}.middlewares={middleware_name}"])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Proto=https",
                ])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Port=443",
                ])
                run_args.extend([
                    "-l",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Ssl=on",
                ])

        # Volumes
        if 'volumes' in metadata:
            for vol in metadata['volumes']:
                run_args.extend(["-v", f"{vol['name']}:{vol['mount_path']}"])

        run_args.append(image)

        # Construct final command string safely
        safe_run = " ".join(shlex.quote(arg) for arg in run_args)

        safe_net = shlex.quote(net)
        net_cmd = (
            f"docker network inspect {safe_net} >/dev/null 2>&1 "
            f"|| docker network create {safe_net} >/dev/null"
        )
        rm_cmd = f"docker rm -f {shlex.quote(name)} || true"

        return f"{net_cmd} && {rm_cmd} && {safe_run}"

    def _update_cloudflare_dns(self, domain, ip, token):
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"

        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': domain}, timeout=30)
        if not resp.ok:
            return

        zones = resp.json().get('result')
        if not zones:
            return
        zone_id = zones[0]['id']

        records_to_update = ['@', '*']

        for name in records_to_update:
            search_name = f"{name}.{domain}" if name != '@' else domain
            resp = requests.get(f"{base_url}/zones/{zone_id}/dns_records",
                                headers=headers,
                                params={'type': 'A', 'name': search_name}, timeout=30)
            if resp.ok:
                results = resp.json().get('result', [])
                for record in results:
                    update_url = f"{base_url}/zones/{zone_id}/dns_records/{record['id']}"
                    payload = {
                        'type': 'A',
                        'name': record['name'],
                        'content': ip,
                        'ttl': record['ttl'],
                        'proxied': record['proxied']
                    }
                    requests.put(update_url, headers=headers, json=payload, timeout=30)

    def _update_service_a_record(self, public_domain, target_ip, token):
        """Create/update a specific A record for the service subdomain, leaving the wildcard intact."""
        config = PlatformConfig.load()
        platform_domain = config.domain
        if not platform_domain:
            return
        domain = str(public_domain or '').strip().lower()
        if not domain.endswith('.' + platform_domain):
            return
        name = domain[:-(len(platform_domain) + 1)]
        if not name:
            return
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"
        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': platform_domain}, timeout=30)
        if not resp.ok:
            return
        zones = resp.json().get('result')
        if not zones:
            return
        zone_id = zones[0]['id']
        search = requests.get(
            f"{base_url}/zones/{zone_id}/dns_records",
            headers=headers,
            params={'type': 'A', 'name': domain},
            timeout=30,
        )
        existing = search.json().get('result', []) if search.ok else []
        payload = {'type': 'A', 'name': name, 'content': target_ip, 'ttl': 1, 'proxied': False}
        if existing:
            record_id = existing[0]['id']
            requests.put(
                f"{base_url}/zones/{zone_id}/dns_records/{record_id}",
                headers=headers, json=payload, timeout=30,
            )
        else:
            requests.post(
                f"{base_url}/zones/{zone_id}/dns_records",
                headers=headers, json=payload, timeout=30,
            )

    def _delete_service_a_record(self, public_domain, token):
        """Delete the specific A record for a service subdomain."""
        config = PlatformConfig.load()
        platform_domain = config.domain
        if not platform_domain:
            return
        domain = str(public_domain or '').strip().lower()
        if not domain.endswith('.' + platform_domain):
            return
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        base_url = "https://api.cloudflare.com/client/v4"
        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': platform_domain}, timeout=30)
        if not resp.ok:
            return
        zones = resp.json().get('result')
        if not zones:
            return
        zone_id = zones[0]['id']
        search = requests.get(
            f"{base_url}/zones/{zone_id}/dns_records",
            headers=headers,
            params={'type': 'A', 'name': domain},
            timeout=30,
        )
        if search.ok:
            for record in search.json().get('result', []):
                requests.delete(
                    f"{base_url}/zones/{zone_id}/dns_records/{record['id']}",
                    headers=headers, timeout=30,
                )
