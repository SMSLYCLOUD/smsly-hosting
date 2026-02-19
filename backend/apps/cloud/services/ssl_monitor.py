"""SSL Monitor service."""
import logging
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from apps.deployments.models import Service, PlatformConfig
from apps.notifications.models import Notification

logger = logging.getLogger(__name__)

class SSLMonitorService:
    def check_all_certificates(self):
        """Check expiry of all SSL certs."""
        config = PlatformConfig.load()
        if not config.use_ssl:
            return

        # Check platform domain
        if config.domain:
            self._check_cert(config.domain)

        # Check custom domains on services
        services = Service.objects.exclude(custom_domains=[])
        for service in services:
            for domain in (service.custom_domains or []):
                self._check_cert(domain, service.owner)

    def _check_cert(self, domain, owner=None):
        import ssl
        import socket

        try:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
                s.settimeout(5.0)
                s.connect((domain, 443))
                cert = s.getpeercert()

            not_after = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
            # Convert to aware UTC
            expires_at = timezone.make_aware(not_after, timezone=timezone.utc)

            days_left = (expires_at - timezone.now()).days

            if days_left < 7:
                self._alert(domain, days_left, owner)
                self._attempt_renew(domain)

        except Exception as e:
            logger.warning(f"SSL check failed for {domain}: {e}")

    def _alert(self, domain, days, owner):
        msg = f"SSL Certificate for {domain} expires in {days} days."
        logger.warning(msg)
        if owner:
            Notification.objects.create(
                user=owner,
                title="SSL Expiry Warning",
                message=msg,
                event_type="ssl_expiring"
            )

    def _attempt_renew(self, domain):
        # Trigger Caddy reload via API or touch file
        # Stub for now
        logger.info(f"Triggering renewal for {domain}")
