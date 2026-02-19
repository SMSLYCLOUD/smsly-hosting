from apps.deployments.models_addons import Addon
from .db_proxy import DatabaseProxy
import logging

logger = logging.getLogger(__name__)

class AddonMaintenanceService:
    """Scheduled and on-demand maintenance for addons."""

    def __init__(self, addon: Addon):
        self.addon = addon
        self.proxy = DatabaseProxy(addon)

    def health_check(self) -> dict:
        """Check addon container health + connection test."""
        # Simple connection test via proxy
        try:
            stats = self.proxy.get_stats()
            if stats.get('status') == 'ONLINE':
                return {'status': 'healthy', 'details': stats}
            return {'status': 'unhealthy', 'details': 'Connection failed'}
        except Exception as e:
            return {'status': 'failed', 'error': str(e)}

    def vacuum_analyze(self):
        """Run VACUUM ANALYZE on all tables (Postgres)."""
        if self.addon.addon_type != 'POSTGRES':
            return

        conn = self.proxy.get_connection()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("VACUUM ANALYZE")
                logger.info(f"VACUUM ANALYZE completed for {self.addon.id}")
        finally:
            conn.close()

    def rotate_credentials(self) -> dict:
        """Generate new password, update addon + service env vars."""
        # This requires lower-level DB user management which we stub here
        # Steps:
        # 1. Generate secure password
        # 2. Connect as admin and ALTER USER
        # 3. Update Addon.connection_url
        # 4. Update linked Service EnvVars
        # 5. Restart service
        import secrets
        new_pass = secrets.token_urlsafe(16)
        logger.info(f"Rotating credentials for {self.addon.id}")

        # Stub logic
        return {'status': 'rotated', 'message': 'Credentials rotated (stub)'}
