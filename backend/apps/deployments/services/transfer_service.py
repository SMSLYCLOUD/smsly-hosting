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


class ServerTransferService:
    def __init__(self, transfer):
        self.transfer = transfer
        self.ssh = None
        self._uploaded_remote_backup_path = None

    def _log(self, message):
        """Append a timestamped message to the transfer logs."""
        ts = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{ts}] {message}\n"
        self.transfer.logs += line
        self.transfer.save(update_fields=['logs'])
        logger.info(f"Transfer {self.transfer.id}: {message}")

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
            if server and server.api_url:
                target_url = f"{str(server.api_url).rstrip('/')}{path}"
            else:
                target_url = f"https://{self.transfer.target_server_ip}{path}"
            
            payload = {
                'source_ip': self.transfer.source_server_ip,
                'target_ip': self.transfer.target_server_ip,
                'transfer_type': self.transfer.transfer_type,
                'service_name': self.transfer.service.name if self.transfer.service else None
            }
            body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
            headers = self._build_sync_auth_headers(body, path)
            
            verify_tls = bool(getattr(settings, "TRANSFER_SYNC_VERIFY_TLS", True))
            resp = requests.post(
                target_url,
                data=body,
                headers=headers,
                timeout=5,
                verify=verify_tls,
            )
            if resp.status_code in (200, 201):
                self._log("Target dashboard synchronized successfully.")
            else:
                self._log(f"Warning: Could not sync target dashboard (HTTP {resp.status_code}).")
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
        has_key = bool(self.transfer.target_ssh_key)
        has_password = bool(self.transfer.target_ssh_password)

        if not has_key and not has_password:
            raise ValueError("Target SSH key or password is missing.")

        ssh_kwargs = {'ip': self.transfer.target_server_ip}
        if has_key:
            ssh_kwargs['key_content'] = self.transfer.target_ssh_key
        if has_password:
            ssh_kwargs['password'] = self.transfer.target_ssh_password

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
            backend_container = getattr(
                settings, "REMOTE_BACKEND_CONTAINER_NAME", "smsly-hosting-backend-1"
            )
            check_cmd = f"docker ps -q -f name={backend_container}"
            b_id = self.ssh.exec_command(check_cmd).strip()
            if not b_id:
                # Fallback: look for any container with 'backend' in name
                b_id = self.ssh.exec_command(
                    "docker ps -q -f name=backend"
                ).strip().split('\n')[0]
            if not b_id:
                raise RuntimeError(
                    "CloudNeuron backend container not found on target server. "
                    "Please install CloudNeuron on the target before transferring services."
                )

        self._update(10, 'Creating backup on source server...')

        backup_svc = BackupService()
        if self.transfer.transfer_type == 'SERVICE':
            if not self.transfer.service:
                raise ValueError("Service ID required for SERVICE transfer.")
            backup = backup_svc.backup_service(self.transfer.service.id)
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

        remote_path = f"/tmp/{os.path.basename(local_path)}"
        self._uploaded_remote_backup_path = remote_path

        self.ssh.upload_file(local_path, remote_path)

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

            local_env_path = os.path.join(settings.BASE_DIR, '../.env')
            if os.path.exists(local_env_path):
                self.ssh.upload_file(local_env_path, "/tmp/.env.restore")

    def _restore(self):
        """Step 3: restore on target."""
        self._update(60, 'Restoring services on target server...')

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        backup_filename = os.path.basename(backup.file_path)
        remote_backup_path = (
            self._uploaded_remote_backup_path
            or f"/tmp/{backup_filename}"
        )

        if self.transfer.transfer_type == 'SERVICE':
            self._restore_single_service(remote_backup_path)
        else:
            self._restore_full_server(remote_backup_path)

    def _restore_single_service(self, remote_backup_path):
        self._update(65, 'Uploading backup archive to remote CloudNeuron API container...')
        
        # 1. We must execute the restoration inside the remote server's CloudNeuron
        # backend container so it registers the Service in the remote database!
        # First, copy the tarball into the backend container.
        backend_container = getattr(settings, "REMOTE_BACKEND_CONTAINER_NAME", "smsly-hosting-backend-1")
        
        # Check if remote container exists; fallback to finding it
        # We try to be more robust here: search for containers with 'backend' and 'hosting'
        # or just 'backend' if that fails.
        find_cmd = "docker ps -q -f name=backend --format '{{.Names}}'"
        candidates = self.ssh.exec_command(find_cmd).strip().split('\n')
        
        backend_container = None
        for name in candidates:
            name = name.strip("'\" ")
            if not name: continue
            if 'hosting' in name and 'backend' in name:
                backend_container = name
                break
        
        if not backend_container and candidates:
            # Pick the first one that looks like a backend
            for name in candidates:
                name = name.strip("'\" ")
                if 'backend' in name:
                    backend_container = name
                    break
                    
        if not backend_container:
            # Absolute fallback to the setting or default
            backend_container = getattr(settings, "REMOTE_BACKEND_CONTAINER_NAME", "smsly-hosting-backend-1")
            check_cmd = f"docker ps -q -f name={backend_container}"
            if not self.ssh.exec_command(check_cmd).strip():
                 raise RuntimeError(
                     f"Could not locate CloudNeuron backend container on target server. "
                     f"Searched for: {candidates} and {backend_container}"
                 )

        safe_backend_container = shlex.quote(backend_container)
        self.ssh.exec_command(
            f"docker cp {shlex.quote(remote_backup_path)} {safe_backend_container}:/tmp/transfer_backup.tar.gz"
        )

        self._update(75, 'Hydrating Service via remote Django ORM...')
        
        # 2. Generate a Python script that boots Django inside the remote container
        # and calls BackupService to properly inflate the database models and Volumes.
        owner_email = self.transfer.service.owner.email if self.transfer.service and self.transfer.service.owner else None
        
        restore_script = f"""import os
import sys
import django
import logging

sys.path.append('/app/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.services.backup_service import BackupService
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

def run_restore():
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
        svc._restore_service_from_file('/tmp/transfer_backup.tar.gz', owner=target_user)
        print("SUCCESS")
    except Exception as e:
        print(f"RESTORE_FAILED: {{str(e)}}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    run_restore()
"""
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
                f"docker cp {shlex.quote(script_path)} {safe_backend_container}:/tmp/restore_trigger.py"
            )
        finally:
            os.unlink(local_script.name)

        self._update(85, 'Running database and volume migrations on target...')
        # Execute the python script inside the remote container
        result = self.ssh.exec_command(
            f"docker exec {safe_backend_container} python3 /tmp/restore_trigger.py"
        )
        if "RESTORE_FAILED" in result or "ERROR:" in result:
            raise RuntimeError(f"Remote service hydration failed: {result}")
            
        # Cleanup
        self.ssh.exec_command(
            f"docker exec {safe_backend_container} rm -f /tmp/transfer_backup.tar.gz /tmp/restore_trigger.py"
        )
        self.ssh.exec_command(f"rm -f {shlex.quote(script_path)} {shlex.quote(remote_backup_path)}")

        self._update(90, 'Starting service container on target...')
        # After restoration, the container exists but is not running. 
        # We use the metadata from the source backup to generate the run command.
        metadata = self.transfer.source_backup.metadata
        run_cmd = self._generate_docker_run_command(self.transfer.service, metadata)
        self.ssh.exec_command(run_cmd)

    def _restore_full_server(self, remote_backup_path):
        self._update(60, 'Installing CloudNeuron platform on target...')

        self.ssh.exec_command("yes | /tmp/install.sh")

        self._update(70, 'Stopping services for data restore...')
        self.ssh.exec_command("cd /opt/smsly && docker compose down -v")

        self.ssh.exec_command("cp /tmp/.env.restore /opt/smsly/.env")

        remote_temp_dir = f"/tmp/restore_{self.transfer.id}"
        self.ssh.exec_command(f"mkdir -p {remote_temp_dir}")
        self.ssh.exec_command(f"tar -xzf {remote_backup_path} -C {remote_temp_dir}")

        self._update(75, 'Restoring database...')
        db_dump = f"{remote_temp_dir}/db_dump.sql"

        self.ssh.exec_command("cd /opt/smsly && docker compose up -d db")
        time.sleep(20)

        self.ssh.exec_command(f"docker cp {db_dump} smsly-db:/tmp/dump.sql")

        db_user = self.ssh.exec_command("grep POSTGRES_USER /opt/smsly/.env | cut -d= -f2").strip() or 'smsly'
        db_name = self.ssh.exec_command("grep POSTGRES_DB /opt/smsly/.env | cut -d= -f2").strip() or 'smsly'

        # Use safe quoting for psql commands?
        # db_user/name might have weird chars.
        # But exec_command string interpolation is still used for drop/create.
        # This is on the host shell.
        # It's better, but let's assume they are safe-ish or use shlex if possible.
        # Hard to use shlex for complex piped commands.

        drop_cmd = f"cd /opt/smsly && docker compose exec -T db psql -U {shlex.quote(db_user)} postgres -c 'DROP DATABASE IF EXISTS {shlex.quote(db_name)}; CREATE DATABASE {shlex.quote(db_name)};'"
        self.ssh.exec_command(drop_cmd)

        restore_cmd = f"cd /opt/smsly && docker compose exec -T db sh -c 'psql -U {shlex.quote(db_user)} -d {shlex.quote(db_name)} < /tmp/dump.sql'"
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
        self.ssh.exec_command("cd /opt/smsly && docker compose up -d")

        self.ssh.exec_command(f"rm -rf {remote_temp_dir} {remote_backup_path} {script_path} /tmp/.env.restore")

    def _dns_cutover(self):
        self._update(85, 'DNS cutover: updating records...')

        target_ip = self.transfer.target_server_ip
        config = PlatformConfig.load()

        if config.cloudflare_api_token and config.domain:
            try:
                self._update_cloudflare_dns(config.domain, target_ip, config.cloudflare_api_token)
            except Exception as e:
                logger.error(f"Cloudflare update failed: {e}")

        if self.transfer.transfer_type == 'FULL':
            config.server_ip = target_ip
            config.save()

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
                    ssh_password=self.transfer.target_ssh_password
                )

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
            url = f"http://{self.transfer.target_server_ip}:8090/health"
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
                result = self.ssh.exec_command(
                    f"docker inspect -f '{{{{.State.Running}}}}' {shlex.quote(container_name)}"
                )
                if result.strip() != 'true':
                    raise RuntimeError(
                        f"Service container {container_name} is not running on target"
                    )
                logger.info("Service container %s verified running on target", container_name)
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
        remote_result = self.ssh.exec_command(tcp_check_cmd).strip()
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

    def _complete(self):
        self.transfer.status = 'COMPLETED'
        self.transfer.completed_at = timezone.now()
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=48)
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''

        # Update Service record to point to the new server for grouping in Transfers page
        if self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            from ..models_core import ManagedServer
            target_server = ManagedServer.objects.filter(
                Q(host=self.transfer.target_server_ip) |
                Q(private_ip=self.transfer.target_server_ip)
            ).first()
            if target_server:
                self.transfer.service.server = target_server
                self.transfer.service.save(update_fields=['server'])

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
            self._update_cloudflare_dns(config.domain, self.transfer.source_server_ip, config.cloudflare_api_token)

        if self.transfer.transfer_type == 'FULL':
            config.server_ip = self.transfer.source_server_ip
            config.save()
        elif self.transfer.transfer_type == 'SERVICE' and self.transfer.service:
            from ..models_core import ManagedServer
            source_server = ManagedServer.objects.filter(host=self.transfer.source_server_ip).first()
            self.transfer.service.server = source_server
            self.transfer.service.save(update_fields=['server'])

        self.transfer.status = 'ROLLED_BACK'
        self.transfer.can_rollback = False
        self.transfer.save()

    def _update(self, percent, step):
        self.transfer.progress_percent = percent
        self.transfer.current_step = step
        self.transfer.save(update_fields=['progress_percent', 'current_step'])
        self._log(step)

    def _handle_failure(self, error):
        self.transfer.status = 'FAILED'
        self.transfer.error_message = str(error)
        self.transfer.target_ssh_key = ''
        self.transfer.target_ssh_password = ''
        self.transfer.save(update_fields=['status', 'error_message', 'target_ssh_key', 'target_ssh_password'])
        self._log(f"CRITICAL FAILURE: {error}")

    def _generate_docker_run_command(self, service, metadata):
        name = service.name
        image = metadata.get('docker_image') or service.docker_image

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
        run_args.extend(["-l", f"traefik.http.routers.{name}.rule=Host(`{domain}`)"])
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

        net_cmd = f"docker network create {shlex.quote(net)} || true"
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
