import re

with open("backend/apps/deployments/services/transfer_service.py", "r") as f:
    content = f.read()

# Fix Single Service Restore logic to actually deploy the container
single_service_patch = """
    try:
        svc = BackupService()
        svc._restore_service_from_file('/tmp/transfer_backup.tar.gz', owner=admin_user)

        # Deploy the restored service so it goes live
        from apps.deployments.models import Service, Deployment
        from apps.cloud.models import CloudProvider
        from apps.deployments.tasks import smart_deploy_task

        with open('/tmp/transfer_backup.tar.gz', 'rb') as f:
            import tarfile, json
            with tarfile.open(fileobj=f, mode='r:gz') as tar:
                meta_member = tar.getmember('metadata.json')
                meta = json.load(tar.extractfile(meta_member))
                restored_name = meta['service_name']

        svc_model = Service.objects.get(name=restored_name)
        provider = CloudProvider.objects.first()
        dep = Deployment.objects.create(
            service=svc_model,
            status='QUEUED',
            commit_message='Restored from transfer'
        )
        smart_deploy_task.delay(str(dep.id), str(provider.id))

        print("SUCCESS")
    except Exception as e:
"""

content = content.replace("""    try:
        svc = BackupService()
        svc._restore_service_from_file('/tmp/transfer_backup.tar.gz', owner=admin_user)
        print("SUCCESS")
    except Exception as e:""", single_service_patch)

# Fix Full Server Restore to deploy ALL containers inside the platform
full_server_patch = """
        self._update(90, 'Starting platform...')
        self.ssh.exec_command("cd /opt/smsly && docker compose up -d")

        self._update(92, 'Deploying restored services and updating Caddy...')
        # Execute deployment trigger in the remote container
        deploy_script = \"\"\"import os
import sys
import django
import logging

sys.path.append('/app/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, Deployment
from apps.cloud.models import CloudProvider
from apps.deployments.tasks import smart_deploy_task

provider = CloudProvider.objects.first()
if provider:
    for svc in Service.objects.filter(deploy_type='DOCKER'):
        print(f"Triggering deploy for {svc.name}")
        dep = Deployment.objects.create(
            service=svc,
            status='QUEUED',
            commit_message='Restored from full server transfer'
        )
        smart_deploy_task.delay(str(dep.id), str(provider.id))
\"\"\"
        import shlex
        import tempfile
        script_path = f"/tmp/deploy_trigger_{self.transfer.id}.py"
        local_script = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
        try:
            local_script.write(deploy_script)
            local_script.close()
            self.ssh.upload_file(local_script.name, script_path)

            backend_container = getattr(settings, "REMOTE_BACKEND_CONTAINER_NAME", "smsly-hosting-backend-1")
            b_id = self.ssh.exec_command(f"docker ps -q -f name={backend_container}").strip()
            if not b_id:
                b_id = self.ssh.exec_command("docker ps -q -f name=backend").strip().split('\\n')[0]
                backend_container = b_id or backend_container

            self.ssh.exec_command(f"docker cp {shlex.quote(script_path)} {backend_container}:/tmp/deploy_trigger.py")
            self.ssh.exec_command(f"docker exec {backend_container} python3 /tmp/deploy_trigger.py")
            self.ssh.exec_command(f"docker exec {backend_container} rm -f /tmp/deploy_trigger.py")
        finally:
            import os
            os.unlink(local_script.name)

        self.ssh.exec_command(f"rm -rf {remote_temp_dir} {remote_backup_path} {script_path} /tmp/.env.restore")
"""

content = content.replace("""        self._update(90, 'Starting platform...')
        self.ssh.exec_command("cd /opt/smsly && docker compose up -d")

        self.ssh.exec_command(f"rm -rf {remote_temp_dir} {remote_backup_path} {script_path} /tmp/.env.restore")""", full_server_patch)

with open("backend/apps/deployments/services/transfer_service.py", "w") as f:
    f.write(content)
