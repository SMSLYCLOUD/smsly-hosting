"""SSL Monitor service."""
import logging
from datetime import datetime, timezone as dt_timezone

from celery import shared_task
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
            expires_at = not_after.replace(tzinfo=dt_timezone.utc)

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
        # Caddy auto-renews certs. Trigger a safe config apply/reload so
        # failed/paused cert jobs are nudged without manual SSH intervention.
        try:
            from services.caddy_manager import generate_caddyfile, apply_caddyfile

            config = PlatformConfig.load()
            caddyfile = generate_caddyfile(config)
            cf_token = (config.cloudflare_api_token or "").strip()
            result = apply_caddyfile(caddyfile, cloudflare_token=cf_token)
            if result.get("ok"):
                logger.info("Triggered Caddy reload for certificate refresh: %s", domain)
            else:
                logger.warning("Caddy reload trigger failed for %s: %s", domain, result.get("message"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Certificate refresh trigger failed for %s: %s", domain, exc)


@shared_task(name="apps.cloud.services.ssl_monitor.check_ssl_certificates_task")
def check_ssl_certificates_task():
    """Periodic SSL certificate expiry monitor."""
    SSLMonitorService().check_all_certificates()
