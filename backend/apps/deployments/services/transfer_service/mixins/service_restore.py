import json
import logging
import re
import shlex

from ..helpers import _safe_service_name

logger = logging.getLogger(__name__)


class SingleServiceRestoreMixin:
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
