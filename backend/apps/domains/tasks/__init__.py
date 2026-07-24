"""Background tasks for custom-domain DNS and SSL provisioning."""

import logging

from apps.deployments.models import PlatformConfig
from celery import shared_task

from apps.domains.models import Domain, DomainStatus
from apps.domains.verification import verify_custom_domain_dns

logger = logging.getLogger(__name__)

ROUTABLE_STATUSES = {
    DomainStatus.DNS_VERIFIED,
    DomainStatus.SSL_PROVISIONING,
    DomainStatus.ACTIVE,
}


@shared_task(name="apps.domains.tasks.verify_dns_and_provision_ssl_task")
def verify_dns_and_provision_ssl_task(domain_id):
    """Verify public DNS and make Caddy eligible to issue direct SSL."""
    try:
        domain = Domain.objects.select_related("service").get(id=domain_id)
    except Domain.DoesNotExist:
        return

    old_status = domain.status
    if old_status not in ROUTABLE_STATUSES:
        domain.status = DomainStatus.DNS_PENDING
        domain.save(update_fields=["status"])

    config = PlatformConfig.load()
    result = verify_custom_domain_dns(domain, config)
    domain.dns_expected = result.expected
    domain.dns_actual = result.actual

    if result.verified:
        domain.status = (
            old_status
            if old_status in {DomainStatus.ACTIVE, DomainStatus.SSL_PROVISIONING}
            else DomainStatus.DNS_VERIFIED
        )
        domain.last_error = None
        domain.verified = True
        domain.save(update_fields=[
            "status",
            "dns_expected",
            "dns_actual",
            "last_error",
            "verified",
        ])

        if old_status not in ROUTABLE_STATUSES:
            logger.info(
                "DNS verified for %s via %s; triggering Caddy reload",
                domain.domain_name,
                result.matched_by or "DNS",
            )
            _trigger_caddy_reload()
        else:
            logger.debug(
                "DNS verification finished for %s; already routable",
                domain.domain_name,
            )
        return

    domain.status = DomainStatus.DNS_PENDING
    domain.last_error = result.error or f"Expected {result.expected} but got {result.actual}."
    domain.verified = False
    domain.ssl_active = False
    domain.save(update_fields=[
        "status",
        "dns_expected",
        "dns_actual",
        "last_error",
        "verified",
        "ssl_active",
    ])

    if old_status in ROUTABLE_STATUSES:
        logger.info(
            "DNS no longer verifies for %s; triggering Caddy reload",
            domain.domain_name,
        )
        _trigger_caddy_reload()


def _trigger_caddy_reload():
    from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

    config = PlatformConfig.load()
    content = generate_caddyfile(config)
    cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
    result = apply_caddyfile(content, cloudflare_token=cf_token)
    if not result.get("ok"):
        logger.error("Caddy reload triggered by domain verification failed: %s", result.get("message", "unknown error"))
