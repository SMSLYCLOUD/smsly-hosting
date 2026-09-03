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

        # PRE-DESTROY SAFETY: if the target already has a platform running,
        # snapshot its .env + volumes dir before `compose down -v` destroys
        # them. A failed transfer can then restore the target's pre-transfer
        # state from these snapshots instead of leaving it wiped.
        snapshot_env = _command_text(self.ssh.exec_command(
            f"cat {quoted_hosting_path}/.env 2>/dev/null | head -c 100000"
        )).strip()
        if snapshot_env:
            self.ssh.exec_command(
                f"cp {quoted_hosting_path}/.env /tmp/.env.target-pre-transfer 2>/dev/null || true"
            )
            self._log("Saved target's pre-transfer .env snapshot to /tmp/.env.target-pre-transfer")

        # Check for existing DB data (volume) and snapshot it
        has_db = _command_text(self.ssh.exec_command(
            f"docker volume ls --format '{{{{.Name}}}}' | grep -c 'db' || true"
        )).strip()
        if has_db and has_db != "0":
            self._log(
                "Target has existing database volumes — consider backing them up "
                "before the restore (they will be destroyed by compose down -v). "
                "A .env snapshot was saved; the DB itself is NOT backed up "
                "automatically (would require the target's disk space)."
            )

        self.ssh.exec_command(f"{compose} down -v; }}")

        self.ssh.exec_command(f"cp /tmp/.env.restore {quoted_hosting_path}/.env")

        # ENCRYPTION KEY PRESERVATION: the fresh install above generated
        # a NEW FIELD_ENCRYPTION_KEY + SECRET_KEY. The restored .env has
        # the SOURCE's keys. Without restoring the source's encryption
        # key, every EncryptedCharField/EncryptedTextField value in the
        # restored DB would be undecryptable (they were encrypted with
        # the source's key). Merge: keep the source's encryption keys
        # but preserve any target-specific networking values (IP, DOMAIN).
        merge_env_cmd = (
            f"cd {quoted_hosting_path} && "
            # Extract source's encryption + secret keys
            "SRC_KEY=$(grep '^FIELD_ENCRYPTION_KEY=' /tmp/.env.restore | cut -d= -f2); "
            "SRC_SECRET=$(grep '^SECRET_KEY=' /tmp/.env.restore | cut -d= -f2); "
            "BACKUP_KEY=$(grep '^BACKUP_ENCRYPTION_KEY=' /tmp/.env.restore | cut -d= -f2); "
            "GW_SECRET=$(grep '^GATEWAY_SECRET=' /tmp/.env.restore | cut -d= -f2); "
            # Replace in the live .env (the restore already overwrote it,
            # but re-merge in case the install regenerated)
            "[ -n \"$SRC_KEY\" ] && sed -i \"s|^FIELD_ENCRYPTION_KEY=.*|FIELD_ENCRYPTION_KEY=$SRC_KEY|\" .env || true; "
            "[ -n \"$SRC_SECRET\" ] && sed -i \"s|^SECRET_KEY=.*|SECRET_KEY=$SRC_SECRET|\" .env || true; "
            "[ -n \"$BACKUP_KEY\" ] && sed -i \"s|^BACKUP_ENCRYPTION_KEY=.*|BACKUP_ENCRYPTION_KEY=$BACKUP_KEY|\" .env || true; "
            "[ -n \"$GW_SECRET\" ] && sed -i \"s|^GATEWAY_SECRET=.*|GATEWAY_SECRET=$GW_SECRET|\" .env || true; "
            "echo ENV_KEYS_MERGED"
        )
        merge_result = _command_text(self.ssh.exec_command(merge_env_cmd, timeout=30))
        if "ENV_KEYS_MERGED" in merge_result:
            self._log("Encryption keys merged from source backup into target .env")
        else:
            self._log(f"WARNING: encryption key merge result: {merge_result[:100]}")

        remote_temp_dir = f"/tmp/restore_{self.transfer.id}"
        self.ssh.exec_command(f"mkdir -p {shlex.quote(remote_temp_dir)}")
        self.ssh.exec_command(f"tar -xzf {shlex.quote(remote_backup_path)} -C {shlex.quote(remote_temp_dir)}")

        self._update(75, 'Restoring database...')
        db_dump = f"{remote_temp_dir}/db_dump.sql"

        self.ssh.exec_command(f"{compose} up -d db; }}")

        # Wait for DB readiness with a health probe instead of a
        # hardcoded sleep(20) — if the DB takes longer, the restore
        # silently fails with a connection refused error.
        db_ready_cmd = (
            f"for i in $(seq 1 60); do "
            f"docker exec smsly-hosting-db-1 pg_isready -U postgres >/dev/null 2>&1 "
            f"&& echo DB_READY && exit 0; "
            f"docker exec smsly-db pg_isready -U postgres >/dev/null 2>&1 "
            f"&& echo DB_READY && exit 0; "
            f"sleep 2; "
            f"done; echo DB_NOT_READY; exit 1"
        )
        db_ready = _command_text(self.ssh.exec_command(db_ready_cmd, timeout=150))
        if "DB_READY" not in db_ready:
            raise RuntimeError(
                "Target database did not become ready before restore "
                "(waited 120s with pg_isready probe)."
            )

        # Detect the CORRECT database container (the PLATFORM's DB, not
        # a tenant service named 'smsly-db'). The old fallback blindly
        # tried 'smsly-db' which could be a completely different DB.
        db_container = _command_text(self.ssh.exec_command(
            f"docker ps --filter name=smsly-hosting-db --format '{{{{.Names}}}}' | head -1"
        )).strip() or "smsly-hosting-db-1"

        # Validate: must be a platform DB container, not a tenant's
        if "hosting" not in db_container:
            db_container = "smsly-hosting-db-1"

        self.ssh.exec_command(f"docker cp {shlex.quote(db_dump)} {shlex.quote(db_container)}:/tmp/dump.sql")

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

        # Use the validated db_container for all psql operations —
        # no blind fallback to 'smsly-db' (a tenant service name).
        drop_cmd = (
            f"{compose} exec -T {shlex.quote(db_container)} psql -U {shlex.quote(db_user)} postgres "
            f"-c {shlex.quote(drop_sql_str)}"
            "; }"
        )
        self.ssh.exec_command(drop_cmd)

        restore_cmd = (
            f"{compose} exec -T {shlex.quote(db_container)} sh -c "
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

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", db_user):
        db_user = "smsly"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", db_name):
        db_name = "smsly"

    # SQL-injection-safe DROP + CREATE using shell-safe quoting.
    # The f-string `{{db_name}}` bug is gone: use explicit variable
    # interpolation AFTER regex validation.
    drop_sql = 'DROP DATABASE IF EXISTS "%s"; CREATE DATABASE "%s";' % (db_name, db_name)

    # Detect the platform's DB container — NEVER fall back to 'smsly-db'
    # (that's a tenant service with a completely different database).
    import subprocess as _sp
    db_container = "smsly-hosting-db-1"
    try:
        ps = _sp.run(
            ["docker", "ps", "--filter", "name=smsly-hosting-db",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=15
        )
        first = (ps.stdout or "").strip().splitlines()
        if first and "hosting" in first[0]:
            db_container = first[0]
    except Exception:
        pass

    subprocess.run(
        ["docker", "exec", db_container, "psql", "-U", db_user, "-d", "postgres", "-c", drop_sql],
        check=True, capture_output=True, text=True, timeout=120
    )

    subprocess.run(["docker", "cp", db_dump, f"{db_container}:/tmp/dump.sql"], check=True)
    restore_result = subprocess.run(
        ["docker", "exec", db_container, "psql", "-U", db_user, "-d", db_name, "-f", "/tmp/dump.sql"],
        capture_output=True, text=True, timeout=600
    )
    if restore_result.returncode != 0:
        raise RuntimeError(f"DB restore failed on {db_container}: " + (restore_result.stderr or "")[-300:])
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

        # Fix: create BOTH the standard networks AND the platform bridge
        # (smsly-platform-net) so restored services get dual-homed
        # (project + platform bridge) for cross-project communication.
        # Also use check=True on network creation failures — capture_output
        # swallows errors.
        start_script = """
import subprocess, os

hosting_path = "/opt/smsly-hosting"
os.chdir(hosting_path)

os.makedirs("caddy-config", exist_ok=True)
os.makedirs("/opt/smsly-cache", exist_ok=True)

# Create platform networks (idempotent — ignore 'already exists')
for net in ("smsly-net", "smsly-proxy", "smsly-platform-net"):
    r = subprocess.run(
        ["docker", "network", "inspect", net],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        subprocess.run(
            ["docker", "network", "create", net],
            capture_output=True, text=True, check=True
        )

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
