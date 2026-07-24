"""RestoreMixin — restore-related transfer methods."""

import base64
import contextlib
import json
import logging
import os
import re
import shlex
import socket
import tempfile
import time

import requests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.deployments.models.core import PlatformConfig

from ...backup_service import BackupService, UnknownBackupKeyIdError
from ..helpers import (
    TRANSFER_ERROR_LIMIT,
    _command_text,
    _redact_transfer_text,
    _safe_backup_basename,
    _safe_service_name,
    get_transfer_log_limit,
)

logger = logging.getLogger(__name__)


class RestoreMixin:
    def _restore(self):
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
            self._restore_full_server_rest(remote_backup_path)

    def _restore_single_service(self, remote_backup_path):
        target_server = self._target_server_record()
        is_lite_agent = getattr(target_server, 'is_lite_agent', False) if target_server else False

        if is_lite_agent:
            self._restore_single_service_lite(remote_backup_path)
            return

        self._update(75, 'Hydrating Service on target via REST API...')
        owner_email = self.transfer.service.owner.email if self.transfer.service and self.transfer.service.owner else None
        restore_script = self._build_restore_trigger_script(owner_email, remote_backup_path)
        exec_result = self._node_api_request('incoming/exec', body={
            'script': restore_script,
            'container': 'backend',
        })
        if exec_result.get('exit_code', 0) != 0:
            raise RuntimeError(f"Remote service hydration failed: {exec_result.get('stdout', '')[:500]}")

        metadata = self.transfer.source_backup.metadata
        image = metadata.get('docker_image') or self.transfer.service.docker_image
        if image:
            self._update(85, 'Pulling service image on target...')
            self._node_api_request('incoming/pull-image', body={'image': image})

        self._remap_target_platform_env()

        from ....models.network_scope import ScopedNetwork
        scoped_net = ScopedNetwork.resolve_network_name(self.transfer.service.project) if self.transfer.service.project else 'smsly-net'

        self._update(90, 'Starting service container on target...')
        env_vars = metadata.get('env_vars', [])
        env_dict = {e['key']: e['value'] for e in env_vars}
        name = self.transfer.service.name
        port = self.transfer.service.internal_port
        domain = self.transfer.service.public_domain
        labels = {
            'traefik.enable': 'true',
            'traefik.docker.network': scoped_net,
            f'traefik.http.routers.{name}.rule': f'Host(`{domain}`)',
            f'traefik.http.routers.{name}.service': name,
            f'traefik.http.services.{name}.loadbalancer.server.port': str(port),
            'managed_by': 'smsly-hosting',
        }

        self._node_api_request('incoming/deploy', body={
            'image': image,
            'container_name': name,
            'env': env_dict,
            'labels': labels,
            'network': scoped_net,
        })
        self._seed_target_deployment_record(metadata)

    def _restore_single_service_lite(self, remote_backup_path):
        self._update(65, 'Restoring service on lite agent target...')

        metadata = self.transfer.source_backup.metadata
        image = metadata.get('docker_image') or self.transfer.service.docker_image

        if image:
            self._update(75, 'Pulling service image on lite agent...')
            self._node_api_request('incoming/pull-image', body={'image': image})

        from ....models.network_scope import ScopedNetwork
        scoped_net = ScopedNetwork.resolve_network_name(self.transfer.service.project) if self.transfer.service.project else 'smsly-net'

        self._update(90, 'Starting service container on lite agent...')
        env_vars = metadata.get('env_vars', [])
        env_dict = {e['key']: e['value'] for e in env_vars}
        name = self.transfer.service.name
        port = self.transfer.service.internal_port
        domain = self.transfer.service.public_domain
        labels = {
            'traefik.enable': 'true',
            'traefik.docker.network': scoped_net,
            f'traefik.http.routers.{name}.rule': f'Host(`{domain}`)',
            f'traefik.http.routers.{name}.service': name,
            f'traefik.http.services.{name}.loadbalancer.server.port': str(port),
            'managed_by': 'smsly-hosting',
        }

        self._node_api_request('incoming/deploy', body={
            'image': image,
            'container_name': name,
            'env': env_dict,
            'labels': labels,
            'network': scoped_net,
        })

    def _remap_target_platform_env(self, backend_container=None):
        if not self.transfer.service:
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
pre_transfer = {}
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
    target_domain = os.environ.get("DOMAIN", "").strip()
    domain_remaps = {}
    if target_domain:
        domain_remaps = {
            "PUBLIC_DOMAIN": target_domain,
            "ALLOWED_HOSTS": f"{target_domain},localhost,127.0.0.1",
            "DJANGO_ALLOWED_HOSTS": target_domain,
            "SITE_URL": f"https://{target_domain}",
        }

    for candidate_key in list(url_remaps.keys()) + list(domain_remaps.keys()):
        env = EnvironmentVariable.objects.filter(service=svc, key=candidate_key).first()
        if env is not None:
            pre_transfer[candidate_key] = str(env.value or "")

    for dk, dv in domain_remaps.items():
        env = EnvironmentVariable.objects.filter(service=svc, key=dk).first()
        if env and env.value and str(env.value).strip():
            old_val = str(env.value).strip()
            old_base = os.environ.get("DOMAIN_OLD", "").strip() or "localhost"
            if old_base in old_val or old_val == "********":
                EnvironmentVariable.objects.update_or_create(
                    service=svc, key=dk,
                    defaults={"value": dv, "source": "SYSTEM"},
                )

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

print("PRE_TRANSFER_ENV_JSON_BEGIN")
print(json.dumps(pre_transfer))
print("PRE_TRANSFER_ENV_JSON_END")
""".strip() % json.dumps(payload)

        pre_transfer: dict = {}
        try:
            exec_result = self._exec_on_target(remap_code)
            output = exec_result.get('stdout', '')
            match = re.search(
                r"PRE_TRANSFER_ENV_JSON_BEGIN\s*(\{.*?\})\s*PRE_TRANSFER_ENV_JSON_END",
                output,
                re.DOTALL,
            )
            if match:
                try:
                    pre_transfer = json.loads(match.group(1)) or {}
                except json.JSONDecodeError as exc:
                    logger.warning("Could not parse pre-transfer env snapshot: %s", exc)
        except Exception as exc:
            logger.warning("Failed to remap target platform env vars: %s", exc)

        if pre_transfer:
            metadata = dict(self.transfer.metadata or {})
            metadata['pre_transfer_env_vars'] = pre_transfer
            self.transfer.metadata = metadata
            self.transfer.save(update_fields=['metadata'])

    def _revert_target_platform_env(self):
        if self.transfer.transfer_type != 'SERVICE' or not self.transfer.service:
            return
        pre_transfer = (self.transfer.metadata or {}).get('pre_transfer_env_vars') or {}
        if not pre_transfer:
            return

        try:
            backend_container = self._find_remote_backend_container(required=False)
        except Exception as exc:
            self._log(f"Could not locate backend container for env revert: {exc}")
            return
        if not backend_container:
            self._log("Backend container not found on target — skipping env revert.")
            return

        service_name = _safe_service_name(self.transfer.service.name)
        shlex.quote(backend_container)
        script_path = f"/tmp/transfer_revert_env_{self.transfer.id}.py"
        shlex.quote(script_path)

        payload = {
            'service_name': service_name,
            'pre_transfer': pre_transfer,
        }
        revert_code = f"""
import json
import os
import sys
import django

for candidate in (os.getcwd(), '/app', '/app/backend'):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable

payload = json.loads({json.dumps(payload)})
svc = Service.objects.filter(name=payload['service_name']).first()
if svc:
    for key, value in payload['pre_transfer'].items():
        EnvironmentVariable.objects.update_or_create(
            service=svc,
            key=key,
            defaults={{
                'value': value,
                'is_secret': True,
                'source': 'SYSTEM',
            }},
        )
    print(f"REVERTED {{len(payload['pre_transfer'])}} env vars for {{payload['service_name']}}")
else:
    print('ERROR: service not found', file=sys.stderr)
"""
        try:
            exec_result = self._exec_on_target(revert_code)
            output = exec_result.get('stdout', '')
            if "REVERTED" in output:
                self._log(f"Reverted target platform env vars: {output.strip()}")
            else:
                self._log(f"Target env revert did not confirm: {output.strip()[:300]}")
        except Exception as exc:
            logger.warning("Failed to revert target platform env vars: %s", exc)

    def _seed_target_deployment_record(self, backend_container=None, metadata=None):
        if not self.transfer.service:
            return

        service_name = _safe_service_name(self.transfer.service.name)
        metadata = metadata or (self.transfer.source_backup.metadata if self.transfer.source_backup else {}) or {}
        image_ref = (
            str(metadata.get('docker_image') or '').strip()
            or str(self.transfer.service.docker_image or '').strip()
            or 'backup-restore'
        )

        payload = {
            'service_name': service_name,
            'image_ref': image_ref,
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

        try:
            self._exec_on_target(restore_code)
        except Exception as exc:
            logger.warning("Failed to seed target deployment record: %s", exc)

    def _load_service_image_on_target(self, remote_backup_path):
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
    owner_email = {owner_email!r}

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
        svc._restore_service_from_file({backup_path!r}, owner=target_user)
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

        from django.db import connection as _django_db_connection
        from psycopg2 import sql as pg_sql

        drop_query = pg_sql.SQL(
            "DROP DATABASE IF EXISTS {}; CREATE DATABASE {};"
        ).format(
            pg_sql.Identifier(db_name),
            pg_sql.Identifier(db_name),
        )
        try:
            with _django_db_connection.cursor() as _cur:
                drop_sql_str = drop_query.as_string(_cur)
        except Exception:
            escaped = db_name.replace('"', '""')
            drop_sql_str = (
                f'DROP DATABASE IF EXISTS "{escaped}"; '
                f'CREATE DATABASE "{escaped}";'
            )

        drop_cmd = (
            f"{compose} exec -T db psql -U {shlex.quote(db_user)} postgres "
            f"-c {shlex.quote(drop_sql_str)}"
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

        restore_script = f"""
import os
import json
import subprocess
import glob

RESTORE_DIR = "{remote_temp_dir}"

def run(cmd):
    subprocess.run(cmd, check=True)

services_dir = os.path.join(RESTORE_DIR, "services")
if os.path.exists(services_dir):
    for tar_file in glob.glob(os.path.join(services_dir, "*.tar.gz")):
        print(f"Restoring {{tar_file}}...")
        svc_tmp = os.path.join(RESTORE_DIR, "svc_tmp")
        os.makedirs(svc_tmp, exist_ok=True)
        run(["tar", "-xzf", tar_file, "-C", svc_tmp])

        run(["docker", "load", "-i", f"{{svc_tmp}}/image.tar"])

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
                except Exception as exc:
                    logger.exception("docker volume create failed for %s: %s", vname, exc)

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

        self._import_backup_key_on_target(remote_temp_dir)

        self.ssh.exec_command(f"rm -rf {remote_temp_dir} {remote_backup_path} {script_path} /tmp/.env.restore")

    def _restore_full_server_rest(self, remote_backup_path):
        backup = self.transfer.source_backup or self.transfer.source_server_backup
        if not backup or not backup.file_path:
            raise ValueError("Backup file not found for FULL transfer.")

        local_path = backup.file_path
        self._update(62, 'Uploading backup to target server...')

        remote_backup = f"/tmp/transfer_backup_{self.transfer.id}.tar.gz"
        file_size = os.path.getsize(local_path)
        self._log(f"Uploading {file_size} bytes to {remote_backup}")

        CHUNK_SIZE = 4 * 1024 * 1024
        with open(local_path, 'rb') as f:
            offset = 0
            chunk_index = 0
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                b64 = base64.b64encode(chunk).decode('ascii')
                self._node_api_request('incoming/upload-file', body={
                    'path': remote_backup,
                    'content_base64': b64,
                    'offset': offset,
                    'chunk_index': chunk_index,
                })
                offset += len(chunk)
                chunk_index += 1
                self._log(f"  Uploaded {offset}/{file_size} bytes")

        self._update(65, 'Extracting backup on target...')

        extract_dir = f"/tmp/restore_{self.transfer.id}"
        extract_script = f"""
import os, json, subprocess, glob

EXTRACT_DIR = "{extract_dir}"
BACKUP = "{remote_backup}"

os.makedirs(EXTRACT_DIR, exist_ok=True)
subprocess.run(["tar", "-xzf", BACKUP, "-C", EXTRACT_DIR], check=True)

for root, dirs, files in os.walk(EXTRACT_DIR):
    for f in files:
        print(os.path.join(root, f))
"""
        self._exec_on_target(extract_script)

        self._update(68, 'Restoring .env on target...')

        env_script = f"""
import os, json

EXTRACT_DIR = "{extract_dir}"
env_path = os.path.join(EXTRACT_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        print("ENV_CONTENT_START")
        print(f.read())
        print("ENV_CONTENT_END")
else:
    print("NO_ENV_FILE")
"""
        env_result = self._exec_on_target(env_script)
        env_output = env_result.get('stdout', '')

        env_content = ''
        if 'ENV_CONTENT_START' in env_output and 'ENV_CONTENT_END' in env_output:
            start = env_output.index('ENV_CONTENT_START') + len('ENV_CONTENT_START')
            end = env_output.index('ENV_CONTENT_END')
            env_content = env_output[start:end].strip()

        if env_content:
            b64_env = base64.b64encode(env_content.encode()).decode('ascii')
            self._node_api_request('incoming/upload-file',
body={
                'path': '/tmp/.env.restore',
                'content_base64': b64_env,
            })

            write_env = """
import subprocess
subprocess.run(["cp", "/tmp/.env.restore", "/opt/smsly-hosting/.env"], check=True)
print("ENV_WRITTEN")
"""
            self._exec_on_target(write_env)

        self._update(72, 'Restoring database on target...')

        restore_db_script = f"""
import os, subprocess, re, json

EXTRACT_DIR = "{extract_dir}"
db_dump = os.path.join(EXTRACT_DIR, "db_dump.sql")

if not os.path.exists(db_dump):
    print("NO_DB_DUMP")
else:
    env_path = "/opt/smsly-hosting/.env"
    db_user = "smsly"
    db_name = "smsly"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("POSTGRES_USER="):
                    db_user = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("POSTGRES_DB="):
                    db_name = line.split("=", 1)[1].strip().strip('"').strip("'")

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{{0,62}}", db_user):
        db_user = "smsly"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{{0,62}}", db_name):
        db_name = "smsly"

    drop_sql = f'DROP DATABASE IF EXISTS "{{db_name}}"; CREATE DATABASE "{{db_name}}";'
    try:
        subprocess.run(
            ["docker", "exec", "smsly-hosting-db-1", "psql", "-U", db_user, "-d", "postgres", "-c", drop_sql],
            check=True, capture_output=True, text=True
        )
    except Exception:
        subprocess.run(
            ["docker", "exec", "smsly-db", "psql", "-U", db_user, "-d", "postgres", "-c", drop_sql],
            check=True, capture_output=True, text=True
        )

    subprocess.run(["docker", "cp", db_dump, "smsly-hosting-db-1:/tmp/dump.sql"], check=True)
    restore_result = subprocess.run(
        ["docker", "exec", "smsly-hosting-db-1", "psql", "-U", db_user, "-d", db_name, "-f", "/tmp/dump.sql"],
        capture_output=True, text=True
    )
    if restore_result.returncode != 0:
        subprocess.run(["docker", "cp", db_dump, "smsly-db:/tmp/dump.sql"], check=True)
        subprocess.run(
            ["docker", "exec", "smsly-db", "psql", "-U", db_user, "-d", db_name, "-f", "/tmp/dump.sql"],
            check=True
        )
    print("DB_RESTORED")
"""
        db_result = self._exec_on_target(restore_db_script)
        if 'DB_RESTORED' not in db_result.get('stdout', ''):
            self._log(f"DB restore warning: {db_result.get('stdout', '')[:300]}")

        self._update(80, 'Restoring service data on target...')

        restore_services_script = f"""
import os, json, subprocess, glob

EXTRACT_DIR = "{extract_dir}"
services_dir = os.path.join(EXTRACT_DIR, "services")

if not os.path.exists(services_dir):
    print("NO_SERVICES_DIR")
else:
    restored = 0
    for tar_file in glob.glob(os.path.join(services_dir, "*.tar.gz")):
        print(f"Restoring {{tar_file}}...")
        svc_tmp = os.path.join(EXTRACT_DIR, "svc_tmp")
        os.makedirs(svc_tmp, exist_ok=True)
        subprocess.run(["tar", "-xzf", tar_file, "-C", svc_tmp], check=True)

        image_tar = os.path.join(svc_tmp, "image.tar")
        if os.path.exists(image_tar):
            subprocess.run(["docker", "load", "-i", image_tar], check=True)

        meta_path = os.path.join(svc_tmp, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                data = json.load(f)
            for vol in data.get("volumes", []):
                vname = vol["name"]
                vfile = vol["filename"]
                vfile_path = os.path.join(svc_tmp, vfile)
                if os.path.exists(vfile_path):
                    try:
                        subprocess.run(["docker", "volume", "create", vname], check=True)
                    except Exception:
                        pass
                    subprocess.run([
                        "docker", "run", "--rm", "-i",
                        "-v", f"{{vname}}:/dest",
                        "-v", f"{{svc_tmp}}:/src",
                        "alpine", "tar", "-xzf", f"/src/{{vfile}}", "-C", "/dest"
                    ], check=True)

        subprocess.run(["rm", "-rf", svc_tmp], check=True)
        restored += 1

    print(f"SERVICES_RESTORED:{{restored}}")
"""
        self._exec_on_target(restore_services_script)

        self._update(88, 'Starting platform on target...')

        start_script = """
import subprocess, os

hosting_path = "/opt/smsly-hosting"
os.chdir(hosting_path)

os.makedirs("caddy-config", exist_ok=True)
os.makedirs("/opt/smsly-cache", exist_ok=True)

subprocess.run(["docker", "network", "inspect", "smsly-net"], capture_output=True)
subprocess.run(["docker", "network", "create", "smsly-net"], capture_output=True)
subprocess.run(["docker", "network", "inspect", "smsly-proxy"], capture_output=True)
subprocess.run(["docker", "network", "create", "smsly-proxy"], capture_output=True)

compose_file = None
for candidate in [
    "infrastructure/docker/docker-compose.agent-lite.yml",
    "docker-compose.prod.yml",
    "docker-compose.yml",
]:
    if os.path.exists(candidate):
        compose_file = candidate
        break

if compose_file:
    subprocess.run(["docker", "compose", "-f", compose_file, "up", "-d", "--build"], check=True)
else:
    subprocess.run(["docker", "compose", "up", "-d", "--build"], check=True)

print("PLATFORM_STARTED")
"""
        self._exec_on_target(start_script)

        self._exec_on_target(f"""
import subprocess, os
subprocess.run(["rm", "-rf", "{extract_dir}", "{remote_backup}", "/tmp/.env.restore"], check=False)
print("CLEANUP_DONE")
""")

    def _import_backup_key_on_target(self, remote_temp_dir: str) -> None:
        if self.transfer.transfer_type != 'FULL':
            return
        if not self.ssh:
            return
        bundle_check = _command_text(self.ssh.exec_command(
            "test -f /tmp/key_export.json && echo PRESENT || echo MISSING",
            raise_on_error=False,
        )).strip()
        if "PRESENT" not in bundle_check:
            return
        try:
            backend_container = self._find_remote_backend_container(required=True)
        except Exception as exc:
            self._log(
                f"Could not find backend container for key import: {exc}. "
                "Historical backups from the source will need to be "
                "manually imported on the target."
            )
            return
        try:
            self._wait_for_remote_backend_ready(backend_container)
        except Exception as exc:
            self._log(
                f"Backend container did not become ready for key import: {exc}. "
                "Continuing without key migration."
            )
            return
        safe_backend_container = shlex.quote(backend_container)
        bundle = _command_text(self.ssh.exec_command("cat /tmp/key_export.json")).strip()
        if not bundle:
            self._log("Key export bundle on target is empty — skipping import.")
            return
        try:
            parsed = json.loads(bundle)
            key_id = parsed.get('key_id', '')
            parsed.get('source_label', 'migrated-from-unknown')
        except Exception as exc:
            self._log(f"Could not parse key export bundle: {exc} — skipping import.")
            return
        if not key_id:
            self._log("Key export bundle missing key_id — skipping import.")
            return
        key_material = parsed.get('key_material', '')
        if not key_material:
            self._log("Key export bundle missing key_material — skipping import.")
            return
        import_script = """
import os
import sys
import json
import django

for candidate in (os.getcwd(), '/app', '/app/backend'):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.services.backup_service import BackupService

KEY_EXPORT_PATH = '/tmp/key_export.json'

def run():
    if not os.path.exists(KEY_EXPORT_PATH):
        print('ERROR: key export not found at ' + KEY_EXPORT_PATH, file=sys.stderr)
        sys.exit(1)
    try:
        with open(KEY_EXPORT_PATH) as f:
            bundle = json.load(f)
    except Exception as exc:
        print(f'ERROR: failed to read key export: {exc}', file=sys.stderr)
        sys.exit(1)
    key_id = bundle.get('key_id', '')
    key_material = bundle.get('key_material', '')
    label = bundle.get('source_label', 'migrated-from-unknown')
    if not key_id or not key_material:
        print('ERROR: key export missing key_id or key_material', file=sys.stderr)
        sys.exit(1)
    try:
        result = BackupService.import_backup_key(
            key_id=key_id,
            key_material=key_material,
            label=label,
        )
        print(f"IMPORTED key_id={result['key_id']} fingerprint={result['fingerprint']} created={result['created']}")
    except Exception as exc:
        print(f'ERROR: failed to import key: {exc}', file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    run()
"""
        script_path = f"/tmp/import_key_{self.transfer.id}.py"
        local_script = tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', prefix=f'import_key_{self.transfer.id}_', delete=False
        )
        try:
            local_script.write(import_script)
            local_script.close()
            self.ssh.upload_file(local_script.name, script_path)
        finally:
            os.unlink(local_script.name)
        self.ssh.exec_command(
            f"docker cp {shlex.quote(script_path)} "
            f"{safe_backend_container}:/tmp/import_key.py"
        )
        result = _command_text(self.ssh.exec_command(
            f"docker exec {safe_backend_container} python3 /tmp/import_key.py"
        ))
        if "IMPORTED" not in result or "ERROR" in result:
            self._log(
                f"BACKUP_ENCRYPTION_KEY import on target did not confirm success: {result}"
            )
        else:
            self._log(
                f"Imported source BACKUP_ENCRYPTION_KEY on target: {result.strip()}"
            )
        self.ssh.exec_command(
            f"docker exec -u 0 {safe_backend_container} sh -lc "
            + shlex.quote("rm -f /tmp/import_key.py /tmp/key_export.json || true"),
            raise_on_error=False,
        )
        self.ssh.exec_command(
            f"rm -f {shlex.quote(script_path)}",
            raise_on_error=False,
        )
