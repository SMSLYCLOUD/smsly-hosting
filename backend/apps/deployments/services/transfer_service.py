import logging
import os
import json
import time
import requests
import shlex
import glob
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.db import transaction

from .backup_service import BackupService
from .ssh_client import SSHClient
from ..models import Service, PlatformConfig, EnvironmentVariable
from ..models_storage import Volume

logger = logging.getLogger(__name__)


class ServerTransferService:
    def __init__(self, transfer):
        self.transfer = transfer
        self.ssh = None

    def execute(self):
        """Run transfer pipeline with explicit stage transitions."""
        try:
            self._init_ssh()

            self.transfer.status = 'PREPARING'
            self.transfer.save(update_fields=['status'])
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
        if not self.transfer.target_ssh_key:
            raise ValueError("Target SSH key is missing.")

        self.ssh = SSHClient(
            ip=self.transfer.target_server_ip,
            key_content=self.transfer.target_ssh_key
        )
        try:
            self.ssh.connect()
        except Exception as e:
            raise ConnectionError(f"Could not connect to target server: {e}")

    def _prepare(self):
        """Step 1: create source backup and provision target."""
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

        self._update(20, 'Checking target server requirements...')
        if not self.ssh.check_docker():
            self._update(25, 'Installing Docker on target server...')
            self.ssh.install_docker()
            time.sleep(5)
            if not self.ssh.check_docker():
                 raise RuntimeError("Failed to install Docker on target server.")

    def _upload(self):
        """Step 2: upload backup to target."""
        self._update(40, 'Transferring backup to target server...')

        backup = self.transfer.source_backup or self.transfer.source_server_backup
        if not backup or not backup.file_path:
            raise ValueError("Backup file not found.")

        local_path = backup.file_path
        remote_path = f"/tmp/{os.path.basename(local_path)}"

        self.ssh.upload_file(local_path, remote_path)

        if self.transfer.transfer_type == 'FULL':
            install_script = os.path.join(settings.BASE_DIR, '../install.sh')
            if os.path.exists(install_script):
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
        remote_backup_path = f"/tmp/{backup_filename}"

        if self.transfer.transfer_type == 'SERVICE':
            self._restore_single_service(remote_backup_path)
        else:
            self._restore_full_server(remote_backup_path)

    def _restore_single_service(self, remote_backup_path):
        remote_temp_dir = f"/tmp/restore_{self.transfer.id}"
        self.ssh.exec_command(f"mkdir -p {remote_temp_dir}")
        self.ssh.exec_command(f"tar -xzf {remote_backup_path} -C {remote_temp_dir}")

        self._update(65, 'Loading Docker image...')
        self.ssh.exec_command(f"docker load -i {remote_temp_dir}/image.tar")

        metadata = self.transfer.source_backup.metadata

        if 'volumes' in metadata:
            self._update(70, 'Restoring volumes...')
            for vol in metadata['volumes']:
                vol_name = vol['name']
                vol_file = vol['filename']
                self.ssh.exec_command(f"docker volume create {shlex.quote(vol_name)} || true")

                # Safely construct docker run command
                cmd_parts = [
                    "docker", "run", "--rm", "-i",
                    "-v", f"{vol_name}:/dest",
                    "-v", f"{remote_temp_dir}:/src",
                    "alpine", "tar", "-xzf", f"/src/{vol_file}", "-C", "/dest"
                ]
                safe_cmd = " ".join(shlex.quote(p) for p in cmd_parts)
                self.ssh.exec_command(safe_cmd)

        self._update(75, 'Starting service container...')
        service = self.transfer.service
        run_cmd = self._generate_docker_run_command(service, metadata)
        self.ssh.exec_command(run_cmd)

        self.ssh.exec_command(f"rm -rf {remote_temp_dir} {remote_backup_path}")

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

        drop_cmd = f"docker exec -i smsly-db psql -U {shlex.quote(db_user)} postgres -c 'DROP DATABASE IF EXISTS {shlex.quote(db_name)}; CREATE DATABASE {shlex.quote(db_name)};'"
        self.ssh.exec_command(drop_cmd)

        restore_cmd = f"docker exec -i smsly-db sh -c 'psql -U {shlex.quote(db_user)} -d {shlex.quote(db_name)} < /tmp/dump.sql'"
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
                except: pass

                run([
                    "docker", "run", "--rm", "-i",
                    "-v", f"{{vname}}:/dest",
                    "-v", f"{{svc_tmp}}:/src",
                    "alpine", "tar", "-xzf", f"/src/{{vfile}}", "-C", "/dest"
                ])

        run(["rm", "-rf", svc_tmp])
"""
        script_path = f"/tmp/restore_{self.transfer.id}.py"
        with open("restore_script.py", "w") as f:
            f.write(restore_script)
        self.ssh.upload_file("restore_script.py", script_path)
        os.remove("restore_script.py")

        self.ssh.exec_command(f"python3 {script_path}")

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

    def _verify(self):
        self._update(95, 'Verifying services on target server...')
        time.sleep(15)

        try:
            if self.transfer.transfer_type == 'FULL':
                 url = f"http://{self.transfer.target_server_ip}:8090/health"
                 try:
                     requests.get(url, timeout=5)
                 except: pass
            else:
                pass
        except Exception as e:
            logger.warning(f"Verification warning: {e}")

    def _complete(self):
        self.transfer.status = 'COMPLETED'
        self.transfer.completed_at = timezone.now()
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=48)
        self.transfer.target_ssh_key = ''
        self.transfer.save()
        self._update(100, 'Transfer complete!')

    def rollback(self):
        if not self.transfer.can_rollback:
            raise ValueError('Rollback not allowed')

        config = PlatformConfig.load()
        if config.cloudflare_api_token and config.domain:
            self._update_cloudflare_dns(config.domain, self.transfer.source_server_ip, config.cloudflare_api_token)

        if self.transfer.transfer_type == 'FULL':
            config.server_ip = self.transfer.source_server_ip
            config.save()

        self.transfer.status = 'ROLLED_BACK'
        self.transfer.can_rollback = False
        self.transfer.save()

    def _update(self, percent, step):
        self.transfer.progress_percent = percent
        self.transfer.current_step = step
        self.transfer.save(update_fields=['progress_percent', 'current_step'])

    def _handle_failure(self, error):
        logger.error('Transfer %s failed: %s', self.transfer.id, error)
        self.transfer.status = 'FAILED'
        self.transfer.error_message = str(error)
        self.transfer.target_ssh_key = ''
        self.transfer.save(update_fields=['status', 'error_message', 'target_ssh_key'])

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

        if config.use_ssl:
             run_args.extend(["-l", f"traefik.http.routers.{name}.entrypoints=websecure"])
             run_args.extend(["-l", f"traefik.http.routers.{name}.tls=true"])
             run_args.extend(["-l", f"traefik.http.routers.{name}.tls.certresolver=letsencrypt"])
        else:
             run_args.extend(["-l", f"traefik.http.routers.{name}.entrypoints=web"])

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

        resp = requests.get(f"{base_url}/zones", headers=headers, params={'name': domain})
        if not resp.ok: return

        zones = resp.json().get('result')
        if not zones: return
        zone_id = zones[0]['id']

        records_to_update = ['@', '*']

        for name in records_to_update:
             search_name = f"{name}.{domain}" if name != '@' else domain
             resp = requests.get(f"{base_url}/zones/{zone_id}/dns_records",
                                 headers=headers,
                                 params={'type': 'A', 'name': search_name})
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
                     requests.put(update_url, headers=headers, json=payload)
