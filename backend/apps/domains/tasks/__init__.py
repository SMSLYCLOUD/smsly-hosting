"""Background tasks for custom-domain DNS and SSL provisioning."""

import logging

from apps.deployments.models import PlatformConfig
from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK, TASK_TIME_LIMIT_STANDARD
from apps.domains.models import Domain, DomainStatus
from apps.domains.verification import verify_custom_domain_dns

logger = logging.getLogger(__name__)

ROUTABLE_STATUSES = {
    DomainStatus.DNS_VERIFIED,
    DomainStatus.SSL_PROVISIONING,
    DomainStatus.ACTIVE,
}


@shared_task(bind=True, name="apps.domains.tasks.verify_dns_and_provision_ssl_task", soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
def verify_dns_and_provision_ssl_task(self, domain_id):
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
    from apps.domains.verification import ensure_verification_token
    ensure_verification_token(domain)
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
        domain.verify_fail_count = 0
        domain.save(update_fields=[
            "status",
            "dns_expected",
            "dns_actual",
            "last_error",
            "verified",
            "verify_fail_count",
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


@shared_task(bind=True, name="apps.domains.tasks.watch_platform_edge_task",
             soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1])
def watch_platform_edge_task(self):
    """Beat every 10 min: verify the platform apex the way outsiders see it."""
    from apps.domains.services.edge_watch import watch_edge_task_body
    try:
        return watch_edge_task_body()
    except Exception as exc:  # never take down the beat worker
        logger.error("Edge watch task failed: %s", exc)
        return {"ok": False, "errors": [str(exc)[:200]]}
