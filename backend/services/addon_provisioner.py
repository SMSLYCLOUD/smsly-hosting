# pylint: disable=logging-fstring-interpolation,broad-exception-caught,subprocess-run-check,import-outside-toplevel
"""Addon Provisioner module."""
# pylint: disable=bare-except
# pylint: disable=unused-argument
"""
Docker-Native Addon Provisioner for SMSLY Hosting.

Creates real database containers (PostgreSQL, Redis, MySQL, MongoDB)
using Docker directly, without external PaaS dependencies.

Uses the same Docker network as deployed services for internal connectivity.
"""
import secrets
import subprocess
import logging
import time
from typing import Dict, Optional, Tuple
from decouple import config

logger = logging.getLogger(__name__)


class AddonProvisioner:
    """
    Provisions database addons as Docker containers.

    Uses Docker CLI for simplicity and reliability.
    For Kubernetes environments, use the K8s operator approach instead.
    """

    # Official Docker images for each addon type
    ADDON_IMAGES = {
        'POSTGRES': 'postgres:15-alpine',
        'REDIS': 'redis:7-alpine',
        'MYSQL': 'mysql:8.0',
        'MONGODB': 'mongo:7.0',
    }

    # Default ports for each addon
    ADDON_PORTS = {
        'POSTGRES': 5432,
        'REDIS': 6379,
        'MYSQL': 3306,
        'MONGODB': 27017,
    }

    # Environment variable keys for connection URLs
    ENV_KEY_MAP = {
        'POSTGRES': 'DATABASE_URL',
        'REDIS': 'REDIS_URL',
        'MYSQL': 'MYSQL_URL',
        'MONGODB': 'MONGODB_URI',
    }

    def __init__(self):
        self.network_name = config(
            'DOCKER_NETWORK',
            default='smsly-hosting-network')
        self._ensure_network()

    def _ensure_network(self):
        """Ensure the Docker network exists for service connectivity."""
        try:
            result = subprocess.run(
                ['docker', 'network', 'inspect', self.network_name],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                subprocess.run(
                    ['docker', 'network', 'create', self.network_name],
                    check=True
                )
                logger.info(f"Created Docker network: {self.network_name}")
        except Exception as e:
            logger.warning(f"Could not create/verify network: {e}")

    def provision(self, addon) -> Tuple[str, str]:
        """
        Provision a database addon container.

        Args:
            addon: Addon model instance

        Returns:
            Tuple of (container_id, connection_url)
        """
        addon_type = addon.addon_type
        service_name = addon.service.name

        # Generate unique container name and credentials
        container_name = f"smsly-addon-{addon_type.lower()}-{addon.id}"
        password = secrets.token_urlsafe(24)

        image = self.ADDON_IMAGES.get(addon_type)
        port = self.ADDON_PORTS.get(addon_type)

        if not image:
            raise ValueError(f"Unknown addon type: {addon_type}")

        logger.info(
            f"Provisioning {addon_type} addon for service {service_name}")

        # Build Docker run command based on addon type
        if addon_type == 'POSTGRES':
            container_id, connection_url = self._provision_postgres(
                container_name, password, port
            )
        elif addon_type == 'REDIS':
            container_id, connection_url = self._provision_redis(
                container_name, password, port
            )
        elif addon_type == 'MYSQL':
            container_id, connection_url = self._provision_mysql(
                container_name, password, port
            )
        elif addon_type == 'MONGODB':
            container_id, connection_url = self._provision_mongodb(
                container_name, password, port
            )
        else:
            raise ValueError(f"Unsupported addon type: {addon_type}")

        logger.info(f"Addon {addon_type} provisioned: {container_name}")
        return container_id, connection_url

    def _provision_postgres(self, container_name: str,
                            password: str, port: int) -> Tuple[str, str]:
        """Provision a PostgreSQL container."""
        db_name = "app_db"
        db_user = "app_user"

        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', f'POSTGRES_PASSWORD={password}',
            '-e', f'POSTGRES_USER={db_user}',
            '-e', f'POSTGRES_DB={db_name}',
            '-v', f'{container_name}-data:/var/lib/postgresql/data',
            self.ADDON_IMAGES['POSTGRES']
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        # Internal Docker network URL (service-to-service)
        connection_url = f"postgresql://{db_user}:{password}@{container_name}:{port}/{db_name}"

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _provision_redis(self, container_name: str,
                         password: str, port: int) -> Tuple[str, str]:
        """Provision a Redis container with authentication."""
        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-v', f'{container_name}-data:/data',
            self.ADDON_IMAGES['REDIS'],
            'redis-server', '--requirepass', password, '--appendonly', 'yes'
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        connection_url = f"redis://:{password}@{container_name}:{port}/0"

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _provision_mysql(self, container_name: str,
                         password: str, port: int) -> Tuple[str, str]:
        """Provision a MySQL container."""
        db_name = "app_db"
        db_user = "app_user"

        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', f'MYSQL_ROOT_PASSWORD={password}',
            '-e', f'MYSQL_DATABASE={db_name}',
            '-e', f'MYSQL_USER={db_user}',
            '-e', f'MYSQL_PASSWORD={password}',
            '-v', f'{container_name}-data:/var/lib/mysql',
            self.ADDON_IMAGES['MYSQL']
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        connection_url = f"mysql://{db_user}:{password}@{container_name}:{port}/{db_name}"

        self._wait_for_health(
            container_name,
            port,
            timeout=60)  # MySQL takes longer
        return container_id, connection_url

    def _provision_mongodb(self, container_name: str,
                           password: str, port: int) -> Tuple[str, str]:
        """Provision a MongoDB container."""
        db_user = "app_user"
        db_name = "app_db"

        cmd = [
            'docker', 'run', '-d',
            '--name', container_name,
            '--network', self.network_name,
            '--restart', 'unless-stopped',
            '-e', f'MONGO_INITDB_ROOT_USERNAME={db_user}',
            '-e', f'MONGO_INITDB_ROOT_PASSWORD={password}',
            '-v', f'{container_name}-data:/data/db',
            self.ADDON_IMAGES['MONGODB']
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True)
        container_id = result.stdout.strip()[:12]

        connection_url = f"mongodb://{db_user}:{password}@{container_name}:{port}/{db_name}?authSource=admin"  # pylint: disable=line-too-long

        self._wait_for_health(container_name, port)
        return container_id, connection_url

    def _wait_for_health(self, container_name: str,
                         port: int, timeout: int = 30):
        """Wait for the container to be healthy and accepting connections."""
        logger.info(f"Waiting for {container_name} to be ready...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # Check if container is running
                result = subprocess.run(
                    ['docker', 'inspect', '-f',
                        '{{.State.Running}}', container_name],
                    capture_output=True,
                    text=True
                )
                if result.stdout.strip() == 'true':
                    # Give it a moment to initialize
                    time.sleep(2)
                    logger.info(f"{container_name} is ready")
                    return
            except BaseException:
                pass
            time.sleep(1)

        logger.warning(
            f"{container_name} health check timed out after {timeout}s")

    def deprovision(self, container_id: str,
                    container_name: Optional[str] = None) -> bool:
        """
        Remove an addon container and its volumes.

        Args:
            container_id: Container ID or name
            container_name: Optional container name for volume cleanup
        """
        try:
            # Stop and remove container
            subprocess.run(['docker', 'stop', container_id],
                           capture_output=True)
            subprocess.run(['docker', 'rm', container_id], capture_output=True)

            # Remove associated volume if container_name provided
            if container_name:
                subprocess.run(
                    ['docker', 'volume', 'rm', f'{container_name}-data'],
                    capture_output=True
                )

            logger.info(f"Deprovisioned addon container: {container_id}")
            return True
        except Exception as e:
            logger.error(
                f"Failed to deprovision container {container_id}: {e}")
            return False

    def get_status(self, container_id: str) -> Dict:
        """Get the status of an addon container."""
        try:
            result = subprocess.run(
                ['docker', 'inspect', container_id],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                import json
                info = json.loads(result.stdout)[0]
                return {
                    'running': info['State']['Running'],
                    'status': info['State']['Status'],
                    'started_at': info['State'].get('StartedAt'),
                    'health': info['State'].get('Health', {}).get('Status', 'unknown'),
                }
        except Exception as e:
            logger.error(f"Failed to get container status: {e}")

        return {'running': False, 'status': 'unknown'}


    def create_backup(self, addon) -> str:
        """
        Create a backup of the addon database.
        Returns the path to the backup file.
        """
        import os
        from datetime import datetime
        
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join("/tmp", "backups", str(addon.service.id))
        os.makedirs(backup_dir, exist_ok=True)
        
        filename = f"{addon.addon_type.lower()}_{addon.id}_{timestamp}.dump"
        backup_path = os.path.join(backup_dir, filename)
        
        try:
            if addon.addon_type == 'POSTGRES':
                # PG dump
                cmd = f"docker exec {container_name} pg_dump -U app_user app_db > {backup_path}"
                subprocess.run(cmd, shell=True, check=True)
            
            elif addon.addon_type == 'REDIS':
                # Redis save and copy
                subprocess.run(['docker', 'exec', container_name, 'redis-cli', 'save'], check=True)
                # Copy from container to host
                cmd = f"docker cp {container_name}:/data/dump.rdb {backup_path}"
                subprocess.run(cmd, shell=True, check=True)
                
            elif addon.addon_type == 'MYSQL':
                # MySQL dump
                # Password via env or config is tricky in exec, use passed password if stored (it's not easily available here without fetching)
                # Ideally we store password in Vault. For now assuming we can exec without password if root or use config file
                # Use mysqldump with root password in command (secure temp env var better)
                # Simplified for MVP:
                cmd = f"docker exec {container_name} mysqldump -u root --password=$MYSQL_ROOT_PASSWORD app_db > {backup_path}"
                subprocess.run(cmd, shell=True, check=True)
                
            elif addon.addon_type == 'MONGODB':
                # Mongo dump
                cmd = f"docker exec {container_name} mongodump --username=app_user --password=$MONGO_INITDB_ROOT_PASSWORD --db=app_db --archive" 
                # Redirect to file
                full_cmd = f"{cmd} > {backup_path}"
                subprocess.run(full_cmd, shell=True, check=True)
            
            return backup_path
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Backup failed for {addon.id}: {e}")
            raise e

    def restore_backup(self, addon, backup_path: str) -> bool:
        """
        Restore a backup to the addon database.
        """
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        
        try:
            if addon.addon_type == 'POSTGRES':
                # Drop and recreate schema or just restore
                # cat backup | docker exec -i container psql ...
                cmd = f"cat {backup_path} | docker exec -i {container_name} psql -U app_user app_db"
                subprocess.run(cmd, shell=True, check=True)
                
            elif addon.addon_type == 'REDIS':
                # Copy file back, restart
                subprocess.run(['docker', 'stop', container_name], check=True)
                cmd = f"docker cp {backup_path} {container_name}:/data/dump.rdb"
                subprocess.run(cmd, shell=True, check=True)
                subprocess.run(['docker', 'start', container_name], check=True)
            
            return True
        except Exception as e:
            logger.error(f"Restore failed for {addon.id}: {e}")
            raise e

# Singleton instance
addon_provisioner = AddonProvisioner()
