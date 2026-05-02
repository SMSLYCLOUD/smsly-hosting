from celery import shared_task
from .models import Domain, DomainStatus
from apps.deployments.models import PlatformConfig
import logging
import socket
import dns.resolver

logger = logging.getLogger(__name__)

@shared_task(name="apps.domains.tasks.verify_dns_and_provision_ssl_task")
def verify_dns_and_provision_ssl_task(domain_id):
    try:
        domain = Domain.objects.get(id=domain_id)
    except Domain.DoesNotExist:
        return

    domain.status = DomainStatus.DNS_PENDING
    domain.save(update_fields=['status'])

    config = PlatformConfig.load()

    # What's the expected IP or CNAME?
    # If using IP mode, expected is server IP.
    # If using domain mode, expected is CNAME to platform domain, OR A to server IP.
    expected_ip = config.server_ip
    expected_cname = config.domain

    domain.dns_expected = f"A record to {expected_ip}" if expected_ip else f"CNAME to {expected_cname}"

    try:
        # First check CNAME if applicable
        if expected_cname:
            try:
                answers = dns.resolver.resolve(domain.domain_name, 'CNAME')
                actual = str(answers[0].target).rstrip('.')
                if actual.lower() == expected_cname.lower():
                    domain.status = DomainStatus.DNS_VERIFIED
                    domain.dns_actual = f"CNAME to {actual}"
                    domain.last_error = None
                    domain.verified = True
                    domain.save(update_fields=['status', 'dns_actual', 'last_error', 'verified'])
                    _trigger_caddy_reload()
                    return
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.DNSException):
                pass

        # Check A record
        try:
            answers = dns.resolver.resolve(domain.domain_name, 'A')
            actual_ip = str(answers[0].address)
            domain.dns_actual = f"A record to {actual_ip}"
        except Exception:
            try:
                answers = dns.resolver.resolve(domain.domain_name, 'AAAA')
                actual_ip = str(answers[0].address)
                domain.dns_actual = f"AAAA record to {actual_ip}"
            except Exception:
                actual_ip = socket.gethostbyname(domain.domain_name)
                domain.dns_actual = f"A record to {actual_ip}"

        if expected_ip and actual_ip == expected_ip:
            domain.status = DomainStatus.DNS_VERIFIED
            domain.last_error = None
            domain.verified = True
            domain.save(update_fields=['status', 'dns_actual', 'last_error', 'verified'])
            _trigger_caddy_reload()
            return

        # In case server IP is not set, resolve the platform domain and compare
        if not expected_ip and expected_cname:
            platform_ip = socket.gethostbyname(expected_cname)
            domain.dns_expected = f"A record to {platform_ip} or CNAME to {expected_cname}"
            if actual_ip == platform_ip:
                domain.status = DomainStatus.DNS_VERIFIED
                domain.last_error = None
                domain.verified = True
                domain.save(update_fields=['status', 'dns_expected', 'dns_actual', 'last_error', 'verified'])
                _trigger_caddy_reload()
                return

        domain.last_error = f"Expected {domain.dns_expected} but got {domain.dns_actual}."
        domain.save(update_fields=['dns_expected', 'dns_actual', 'last_error'])

    except Exception as e:
        domain.dns_actual = "None"
        domain.last_error = str(e)
        domain.save(update_fields=['dns_actual', 'last_error'])


def _trigger_caddy_reload():
    from services.caddy_manager import generate_caddyfile, apply_caddyfile
    from apps.deployments.models import PlatformConfig
    config = PlatformConfig.load()
    content = generate_caddyfile(config)
    cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
    apply_caddyfile(content, cloudflare_token=cf_token)
