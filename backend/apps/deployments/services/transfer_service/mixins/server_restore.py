import logging
import os
import re
import shlex
import tempfile
import time

from ..helpers import _command_text

logger = logging.getLogger(__name__)


class ServerRestoreMixin:
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
        import base64

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
                    except Exception as exc:
                        logger.warning("Failed to create docker volume %s during restore: %s", vname, exc)
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
