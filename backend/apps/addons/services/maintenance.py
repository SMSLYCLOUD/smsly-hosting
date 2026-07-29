import logging

from apps.deployments.models.addons import Addon

from .db_proxy import DatabaseProxy

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
        logger.warning(
            "Credential rotation requested for addon %s (%s) but backend implementation is not available.",
            self.addon.id,
            self.addon.addon_type,
        )
        return {
            'status': 'not_implemented',
            'error': (
                'Credential rotation is not implemented for this addon provider yet. '
                'Use manual rotation until provider-level credential mutation is wired.'
            ),
        }
