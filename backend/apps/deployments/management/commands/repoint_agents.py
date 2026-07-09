"""
Management command to re-point all lite agents to a new master after DR promotion.

Usage:
    python manage.py repoint_agents --master-ip=203.0.113.5 --gateway-secret=<secret>

This SSHes into each online lite agent, updates the .env with the new master's
connection info, and restarts the agent's backend to pick up the new DB URL.
"""
import logging
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Re-point all lite agents to a new master after DR promotion"

    def add_arguments(self, parser):
        parser.add_argument('--master-ip', required=True, help='New master public IP')
        parser.add_argument('--master-mesh-ip', default='', help='New master WireGuard mesh IP (defaults to master-ip)')
        parser.add_argument('--gateway-secret', required=True, help='New master GATEWAY_SECRET')
        parser.add_argument('--db-password', default='', help='DB password (read from .promoted-master.json if omitted)')
        parser.add_argument('--dry-run', action='store_true', help='Print actions without executing')

    def handle(self, *args, **options):
        master_ip = options['master_ip']
        master_mesh_ip = options.get('master_mesh_ip') or master_ip
        gateway_secret = options['gateway_secret']
        db_password = options.get('db_password') or self._read_db_password()
        dry_run = options['dry_run']

        # Load promoted-master.json for DB credentials if available
        promote_file = os.path.join(settings.BASE_DIR, '..', '.promoted-master.json')
        if not db_password and os.path.exists(promote_file):
            import json
            with open(promote_file) as f:
                meta = json.load(f)
            db_password = meta.get('db_password', '')
            if not master_mesh_ip or master_mesh_ip == master_ip:
                master_mesh_ip = meta.get('master_mesh_ip', master_ip)

        from apps.deployments.models_servers import ManagedServer

        agents = ManagedServer.objects.filter(
            is_lite_agent=True,
            status=ManagedServer.Status.ONLINE,
        )

        if not agents.exists():
            self.stdout.write(self.style.WARNING('No online lite agents found.'))
            return

        self.stdout.write(f"Re-pointing {agents.count()} lite agent(s) to new master {master_ip}")
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made'))

        success = 0
        failed = 0

        for agent in agents:
            self.stdout.write(f"  Processing agent: {agent.name} ({agent.host})...")

            if dry_run:
                self.stdout.write(f"    Would update .env and restart backend on {agent.host}")
                success += 1
                continue

            try:
                self._repoint_agent(agent, master_ip, master_mesh_ip, gateway_secret, db_password)
                self.stdout.write(self.style.SUCCESS(f"    ✓ {agent.name} re-pointed"))
                success += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    ✗ {agent.name} failed: {e}"))
                failed += 1

        self.stdout.write(f"\nDone: {success} succeeded, {failed} failed")

    def _read_db_password(self):
        """Try to read DB password from the local .env."""
        env_path = os.path.join(settings.BASE_DIR, '..', '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('POSTGRES_PASSWORD=') or line.startswith('DB_PASSWORD='):
                        return line.split('=', 1)[1]
        return ''

    def _repoint_agent(self, agent, master_ip, master_mesh_ip, gateway_secret, db_password):
        """SSH into a lite agent and update its .env to point at the new master."""
        from apps.deployments.services.ssh_client import SSHClient

        ssh = SSHClient(
            ip=agent.host,
            key_content=agent.ssh_key,
            password=agent.ssh_password,
            port=agent.ssh_port or 22,
            user=agent.ssh_user or 'root',
        )
        ssh.connect()

        env_updates = {
            'MASTER_IP': master_ip,
            'MASTER_MESH_IP': master_mesh_ip,
            'GATEWAY_SECRET': gateway_secret,
        }
        if db_password:
            env_updates['MASTER_DB_PASSWORD'] = db_password

        for key, value in env_updates.items():
            cmd = (
                f"grep -q '^{key}=' /opt/smsly-hosting/.env "
                f"&& sed -i 's|^{key}=.*|{key}={value}|' /opt/smsly-hosting/.env "
                f"|| echo '{key}={value}' >> /opt/smsly-hosting/.env"
            )
            ssh.exec_command(cmd, raise_on_error=False)

        # Rebuild the agent's DATABASE_URL from the new master's credentials
        rebuild_cmd = (
            "cd /opt/smsly-hosting && "
            "DB_USER=$(grep -m1 '^MASTER_DB_USER=' .env | cut -d= -f2-) && "
            "DB_PASS=$(grep -m1 '^MASTER_DB_PASSWORD=' .env | cut -d= -f2-) && "
            "DB_NAME=$(grep -m1 '^POSTGRES_DB=' .env 2>/dev/null || echo 'smsly_hosting') && "
            f"NEW_URL=\"postgresql://${{DB_USER}}:${{DB_PASS}}@{master_mesh_ip}:5432/${{DB_NAME}}\" && "
            "grep -q '^DATABASE_URL=' .env && "
            "sed -i \"s|^DATABASE_URL=.*|DATABASE_URL=${NEW_URL}|\" .env || "
            "echo \"DATABASE_URL=${NEW_URL}\" >> .env"
        )
        ssh.exec_command(rebuild_cmd, raise_on_error=False)

        # Restart the agent's backend to pick up new DB URL
        ssh.exec_command(
            "cd /opt/smsly-hosting && "
            "docker compose -f infrastructure/docker/docker-compose.agent-lite.yml restart backend",
            raise_on_error=False,
        )

        # Verify the agent's backend is healthy
        time.sleep(5)
        verify_cmd = (
            "curl -sS --max-time 10 http://localhost:8000/health/live 2>/dev/null "
            "|| echo 'UNHEALTHY'"
        )
        result = ssh.exec_command(verify_cmd, raise_on_error=False)
        status = result[0] if isinstance(result, tuple) else str(result)
        if 'UNHEALTHY' in str(status):
            logger.warning("Agent %s backend may not be healthy after re-point", agent.name)

        ssh.close()
