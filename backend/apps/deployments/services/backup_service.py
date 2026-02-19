import os
import io
import tarfile
import json
import uuid
import logging
from django.utils import timezone
from django.conf import settings
from apps.deployments.models import Service, EnvironmentVariable
from apps.deployments.models_backup import ServiceBackup, ServerBackup
from apps.deployments.models_addons import Addon
from apps.deployments.models_storage import Volume

logger = logging.getLogger(__name__)

class BackupService:
    def backup_service(self, service_id):
        service = Service.objects.get(id=service_id)
        backup = ServiceBackup.objects.create(
            service=service,
            status='IN_PROGRESS'
        )
        try:
            # Snapshot env vars
            env_vars = list(EnvironmentVariable.objects.filter(service=service).values('key', 'value', 'is_secret'))

            # Save metadata
            metadata = {
                'service_name': service.name,
                'deploy_type': service.deploy_type,
                'env_vars': env_vars,
                'git_url': service.repository_url,
                'created_at': str(timezone.now())
            }
            backup.metadata = metadata

            # Mock file creation (Real impl would use docker export)
            backups_dir = os.path.join(settings.BASE_DIR, 'backups', 'services')
            os.makedirs(backups_dir, exist_ok=True)

            filename = f"backup_{service.name}_{uuid.uuid4().hex[:8]}.tar.gz"
            filepath = os.path.join(backups_dir, filename)

            with tarfile.open(filepath, "w:gz") as tar:
                # Add metadata
                metadata_json = json.dumps(metadata, indent=2)
                tarinfo = tarfile.TarInfo(name="metadata.json")
                tarinfo.size = len(metadata_json)
                tar.addfile(tarinfo, io.BytesIO(metadata_json.encode('utf-8')))

            backup.file_path = filepath
            backup.status = 'COMPLETED'
            backup.size_bytes = os.path.getsize(filepath)
            backup.completed_at = timezone.now()
            backup.save()
            return backup

        except Exception as e:
            backup.status = 'FAILED'
            backup.error_message = str(e)
            backup.save()
            logger.error(f"Backup failed for service {service.name}: {e}")
            raise e

    def restore_service(self, backup_id, target_service_id=None):
        backup = ServiceBackup.objects.get(id=backup_id)
        if not target_service_id:
            # Restore to original service (overwrite or alongside?)
            # Usually creates a new deployment on the same service
            target_service = backup.service
        else:
            target_service = Service.objects.get(id=target_service_id)

        # Logic to restore env vars and trigger deployment
        # For now, just log
        logger.info(f"Restoring backup {backup.id} to service {target_service.name}")

        # Restore env vars
        if 'env_vars' in backup.metadata:
            for env in backup.metadata['env_vars']:
                EnvironmentVariable.objects.update_or_create(
                    service=target_service,
                    key=env['key'],
                    defaults={'value': env['value'], 'is_secret': env['is_secret']}
                )

        # Trigger deployment (stub)
        return True

    def backup_server(self):
        # Create ServerBackup
        backup = ServerBackup.objects.create(status='IN_PROGRESS')
        try:
            # Iterate all services and back them up
            services = Service.objects.all()
            included = []

            # Create a master tarball containing all service backups + platform config
            backups_dir = os.path.join(settings.BASE_DIR, 'backups', 'server')
            os.makedirs(backups_dir, exist_ok=True)
            filename = f"server_backup_{uuid.uuid4().hex[:8]}.tar.gz"
            filepath = os.path.join(backups_dir, filename)

            with tarfile.open(filepath, "w:gz") as tar:
                for service in services:
                    # Create individual service backup
                    sb = self.backup_service(service.id)
                    included.append(str(sb.id))
                    # Add to tar
                    if sb.file_path and os.path.exists(sb.file_path):
                        tar.add(sb.file_path, arcname=f"services/{os.path.basename(sb.file_path)}")

            backup.services_included = included
            backup.file_path = filepath
            backup.status = 'COMPLETED'
            backup.size_bytes = os.path.getsize(filepath)
            backup.completed_at = timezone.now()
            backup.save()
            return backup

        except Exception as e:
            backup.status = 'FAILED'
            backup.save()
            raise e

    def restore_server(self, backup_id):
        pass
