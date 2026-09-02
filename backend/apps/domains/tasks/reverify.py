"""Continuous re-verification of custom domains (anti-hijack demotion).

Domain verification was ONE-SHOT: once `verified=True`, the domain
stayed routable forever, even if the owner repointed DNS elsewhere (or
never really controlled it — see the rebinding fix in verification.py).
An attacker who briefly pointed a domain at the platform kept their
cert + routing indefinitely.

This beat task (hourly) re-runs the QUORUM verification for every
active custom domain. Domains that no longer point at the platform are
demoted: verified=False, ssl_active=False, status=DNS_PENDING — which
removes them from Caddy's authorized on_demand set and the routing
config on the next Caddyfile regeneration (also triggered here).
"""
from __future__ import annotations

import logging

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_STANDARD

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
    name="apps.domains.tasks.reverify_custom_domains_task",
)
def reverify_custom_domains_task(self):
    """Re-quorum every ACTIVE/verified custom domain; demote failures."""
    from apps.domains.models import Domain, DomainStatus
    from apps.domains.verification import verify_custom_domain_dns
    from apps.deployments.models import PlatformConfig

    config = PlatformConfig.load()
    demoted, verified_ok = [], []

    # Only re-check domains that are actively trusted.
    candidates = Domain.objects.filter(
        verified=True,
    ).filter(
        status__in=[
            DomainStatus.ACTIVE,
            DomainStatus.DNS_VERIFIED,
            DomainStatus.SSL_PROVISIONING,
        ],
    ).select_related("service")

    for domain in candidates:
        try:
            result = verify_custom_domain_dns(domain, config)
        except Exception as exc:
            logger.warning(
                "reverify: %s raised %s; treating as unverifiable this pass",
                domain.domain_name, exc,
            )
            continue  # transient resolver failure ≠ demotion

        if result.verified:
            verified_ok.append(domain.domain_name)
            continue

        # Demote — DNS no longer points at the platform.
        domain.verified = False
        domain.ssl_active = False
        domain.status = DomainStatus.DNS_PENDING
        domain.last_error = (
            f"Continuous re-verification failed: {result.error or result.actual}"
        )
        domain.save(update_fields=[
            "verified", "ssl_active", "status", "last_error", "updated_at",
        ])
        demoted.append(domain.domain_name)
        logger.warning(
            "reverify: DEMOTED %s (service %s) — %s",
            domain.domain_name,
            getattr(domain.service, "name", "?"),
            result.actual,
        )

    if demoted:
        # Regenerate Caddy so demoted domains lose routing + on-demand
        # TLS eligibility immediately.
        try:
            from apps.deployments.tasks.deploy.caddy import sync_caddy_task
            sync_caddy_task.delay()
        except Exception as exc:
            logger.error("reverify: caddy resync dispatch failed: %s", exc)

    logger.info(
        "reverify: %d ok, %d demoted%s",
        len(verified_ok), len(demoted),
        f" ({', '.join(demoted[:6])})" if demoted else "",
    )
    return {
        "status": "ok",
        "verified_ok": len(verified_ok),
        "demoted": demoted,
    }
