"""SSL Monitor service."""
import ipaddress
import logging
import socket
from datetime import UTC, datetime

from apps.deployments.models import PlatformConfig
from apps.domains.models import Domain, DomainStatus
from apps.domains.tasks import verify_dns_and_provision_ssl_task
from apps.notifications.models import Notification
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _is_safe_outbound_target(host: str) -> bool:
    """SEC (Issue 54): reject outbound targets that resolve to internal IPs.

    The platform connects to arbitrary user-supplied domains to fetch
    certificates; without this check a hostile Domain record (or a
    platform-config typo) could be pointed at ``169.254.169.254``,
    ``localhost``, or RFC1918 space and turn the monitor into an SSRF
    primitive.  We resolve once and refuse any address in a private
    or loopback range.
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError, ValueError):
        return False
    for info in infos or []:
        sockaddr = info[4] if len(info) > 4 else None
        addr = sockaddr[0] if sockaddr else None
        if not addr:
            continue
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True

class SSLMonitorService:
    def check_all_certificates(self):
        """Check expiry of all SSL certs and retry pending DNS."""
        config = PlatformConfig.load()
        if not config.use_ssl:
            return

        # Check platform domain
        if config.domain:
            self._check_cert_platform(config.domain)

        # Retry DNS pending domains
        for domain_obj in Domain.objects.filter(status__in=[DomainStatus.PENDING, DomainStatus.DNS_PENDING]):
            verify_dns_and_provision_ssl_task.delay(domain_obj.id)

        # Check custom domains on services
        for domain_obj in Domain.objects.exclude(status__in=[DomainStatus.PENDING, DomainStatus.DNS_PENDING]):
            self._check_cert_domain_obj(domain_obj)

    def _check_cert_platform(self, domain):
        import socket
        import ssl
        try:
            if not _is_safe_outbound_target(domain):
                logger.warning(
                    "SSL check skipped for platform domain %s: resolves to internal address",
                    domain,
                )
                return
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
                s.settimeout(5.0)
                s.connect((domain, 443))
                cert = s.getpeercert()

            not_after = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
            expires_at = not_after.replace(tzinfo=UTC)

            days_left = (expires_at - timezone.now()).days

            if days_left < 7:
                logger.warning(f"SSL Certificate for {domain} expires in {days_left} days.")
                self._attempt_renew(domain)
        except Exception as e:
            logger.warning(f"SSL check failed for platform domain {domain}: {e}")

    def _check_cert_domain_obj(self, domain_obj):
        import socket
        import ssl
        domain = domain_obj.domain_name
        owner = domain_obj.service.owner

        try:
            if not _is_safe_outbound_target(domain):
                logger.warning(
                    "SSL check skipped for %s: resolves to internal address",
                    domain,
                )
                domain_obj.ssl_active = False
                domain_obj.last_error = "Domain resolves to an internal address."
                domain_obj.checked_at = timezone.now()
                domain_obj.save(update_fields=['ssl_active', 'last_error', 'checked_at'])
                return

            ctx = ssl.create_default_context()
            with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
                s.settimeout(5.0)
                s.connect((domain, 443))
                cert = s.getpeercert()

            not_after = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
            expires_at = not_after.replace(tzinfo=UTC)

            domain_obj.expires_at = expires_at
            domain_obj.status = DomainStatus.ACTIVE
            domain_obj.ssl_active = True
            domain_obj.last_error = None
            domain_obj.save(update_fields=['expires_at', 'status', 'ssl_active', 'last_error'])

            days_left = (expires_at - timezone.now()).days

            if days_left < 7:
                self._alert(domain, days_left, owner)
                self._attempt_renew(domain)

        except Exception as e:
            domain_obj.ssl_active = False
            domain_obj.last_error = str(e)
            domain_obj.checked_at = timezone.now()
            # Only mark SSL_FAILED after consecutive failures (transient protection).
            fail_count = domain_obj.ssl_fail_count + 1
            domain_obj.ssl_fail_count = fail_count
            if fail_count >= 3 and domain_obj.status == DomainStatus.ACTIVE:
                domain_obj.status = DomainStatus.SSL_FAILED
            domain_obj.save(update_fields=['ssl_active', 'status', 'last_error', 'checked_at', 'ssl_fail_count'])
            logger.warning("SSL check failed for %s (attempt %d): %s", domain, fail_count, e)

    def _alert(self, domain, days, owner):
        msg = f"SSL Certificate for {domain} expires in {days} days."
        logger.warning(msg)
        if owner:
            try:
                from apps.notifications.tasks import notify_ssl_expiring
                notify_ssl_expiring.delay(owner.id, domain, days)
            except Exception as e:
                logger.error("Failed to queue SSL expiry notification: %s", e)

    def _attempt_renew(self, domain):
        # Caddy auto-renews certs. Trigger a safe config apply/reload so
        # failed/paused cert jobs are nudged without manual SSH intervention.
        try:
            from services.caddy_manager import apply_caddyfile, generate_caddyfile

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
