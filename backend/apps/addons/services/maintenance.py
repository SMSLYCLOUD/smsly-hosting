import logging
import secrets

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
        """Generate new password, update addon + service env vars.

        Supported: POSTGRES (ALTER USER), REDIS (CONFIG SET requirepass).
        Other addon types return ``not_implemented``.
        """
        from urllib.parse import urlparse, urlunparse

        addon = self.addon
        addon_type = addon.addon_type
        if addon_type not in ('POSTGRES', 'REDIS'):
            return {
                'status': 'not_implemented',
                'error': (
                    'Credential rotation is not implemented for this addon provider yet. '
                    'Use manual rotation until provider-level credential mutation is wired.'
                ),
            }

        from urllib.parse import quote
        new_password = secrets.token_urlsafe(48)

        try:
            if addon_type == 'POSTGRES':
                conn = self.proxy.get_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            'ALTER USER %s WITH PASSWORD %s',
                            (self._pg_user_from_url(addon.connection_url), new_password),
                        )
                        conn.commit()
                finally:
                    conn.close()
            else:  # REDIS
                r = self.proxy.get_connection()
                try:
                    r.config_set('requirepass', new_password)
                finally:
                    r.close()

                # Persist the new password so it survives container restarts.
                # Redis auth is baked into the container env at creation time,
                # so the container must be recreated (data volume + published
                # host port are preserved). This covers addons running on the
                # master (primary + lite-agent nodes); remote full-stack nodes
                # are unreachable through the proxy, so CONFIG SET above would
                # already have failed for them.
                try:
                    from apps.addons.services.addon_provisioner import addon_provisioner
                    parsed_url = urlparse(addon.connection_url or '')
                    container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
                    alias = None
                    hostname = parsed_url.hostname
                    if hostname and hostname != container_name and not self._is_ip_address(hostname):
                        alias = hostname
                    internal_port = int(
                        addon_provisioner.ADDON_PORTS.get(addon.addon_type, 6379) or 6379
                    )
                    addon_provisioner.rotate_redis_credentials(
                        addon, container_name, new_password, internal_port,
                        alias_name=alias, public_domain=addon.public_domain,
                    )
                except Exception as persist_exc:
                    # Roll back the live password so stored credentials stay valid.
                    try:
                        r2 = self.proxy.get_connection()
                        try:
                            r2.config_set('requirepass', parsed_url.password)
                        finally:
                            r2.close()
                    except Exception:
                        logger.exception(
                            "Failed to roll back Redis password after persistence failure for %s",
                            addon.id,
                        )
                    raise persist_exc

            # Rewrite the persisted connection URL with the new password,
            # preserving scheme/user/host/port/database exactly.
            parsed = urlparse(addon.connection_url or '')
            username = parsed.username or ''
            db_path = parsed.path or ''
            new_url = urlunparse((
                parsed.scheme,
                f"{quote(username, safe='')}:{quote(new_password, safe='')}@{parsed.hostname}:{parsed.port}",
                db_path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))
            addon.connection_url = new_url
            addon.save(update_fields=['connection_url', 'updated_at'])

            # Re-inject credentials as env vars (mirrors provision_addon_task)
            from apps.deployments.models import EnvironmentVariable
            creds = addon.parsed_credentials
            for key, value in creds.items():
                EnvironmentVariable.objects.update_or_create(
                    service=addon.service,
                    key=key,
                    defaults={
                        'value': value,
                        'is_secret': key.endswith('_PASSWORD') or key.endswith('_URL'),
                        'source': 'ADDON',
                    },
                )

            return {
                'status': 'success',
                'message': (
                    f'Credentials rotated for {addon.name}. '
                    'Redeploy the service to pick up the new credentials.'
                ),
            }
        except Exception as e:
            logger.error("Credential rotation failed for addon %s: %s", addon.id, e)
            return {'status': 'failed', 'error': str(e)}

    @staticmethod
    def _is_ip_address(value: str) -> bool:
        import ipaddress
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    @staticmethod
    def _pg_user_from_url(connection_url: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(connection_url or '')
        return parsed.username or 'app_user'
