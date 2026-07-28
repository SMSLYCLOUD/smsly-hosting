import json
import logging
import os
import shlex
import tempfile

from ..helpers import _command_text, _safe_service_name

logger = logging.getLogger(__name__)


class TargetSetupMixin:
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
